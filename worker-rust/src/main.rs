//! whirr-worker: Lightweight Rust worker for GPU job orchestration
//!
//! Connects to a whirr server, claims jobs, executes them, and reports results.
//! Designed for minimal memory footprint on GPU machines.

mod client;
mod runner;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use std::{env, thread};

use clap::Parser;
use log::{error, info, warn};

use client::WhirrClient;
use runner::JobRunner;

/// Lightweight worker for whirr GPU job orchestration
#[derive(Parser, Debug)]
#[command(name = "whirr-worker")]
#[command(version, about, long_about = None)]
struct Args {
    /// Server URL (e.g., http://head-node:8080)
    #[arg(short, long, env = "WHIRR_SERVER_URL")]
    server: String,

    /// Data directory for run outputs (shared filesystem)
    #[arg(short, long, env = "WHIRR_DATA_DIR")]
    data_dir: PathBuf,

    /// GPU index to use
    #[arg(short, long)]
    gpu: Option<u32>,

    /// Poll interval in seconds
    #[arg(long, default_value = "5")]
    poll_interval: u64,

    /// Heartbeat interval in seconds
    #[arg(long, default_value = "30")]
    heartbeat_interval: u64,

    /// Lease duration in seconds
    #[arg(long, default_value = "60")]
    lease_seconds: u64,
}

fn main() {
    // Initialize logging
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let args = Args::parse();

    // Set CUDA_VISIBLE_DEVICES if GPU specified
    if let Some(gpu) = args.gpu {
        env::set_var("CUDA_VISIBLE_DEVICES", gpu.to_string());
        info!("Set CUDA_VISIBLE_DEVICES={}", gpu);
    }

    // Generate worker ID
    let hostname = gethostname();
    let worker_id = match args.gpu {
        Some(gpu) => format!("{}:gpu{}", hostname, gpu),
        None => format!("{}:default", hostname),
    };

    info!("Starting whirr-worker: {}", worker_id);
    info!("Server: {}", args.server);
    info!("Data directory: {}", args.data_dir.display());

    // Ensure data directory exists
    let runs_dir = args.data_dir.join("runs");
    if let Err(e) = std::fs::create_dir_all(&runs_dir) {
        error!("Failed to create runs directory: {}", e);
        std::process::exit(1);
    }

    // Setup shutdown signal
    let shutdown = Arc::new(AtomicBool::new(false));
    let shutdown_clone = shutdown.clone();

    ctrlc::set_handler(move || {
        if shutdown_clone.load(Ordering::SeqCst) {
            // Second Ctrl+C - force exit
            warn!("Force shutdown requested");
            std::process::exit(1);
        }
        info!("Shutdown requested, finishing current job...");
        shutdown_clone.store(true, Ordering::SeqCst);
    })
    .expect("Failed to set Ctrl+C handler");

    // Create client
    let client = WhirrClient::new(&args.server);

    // Register with server
    let gpu_ids = args.gpu.map(|g| vec![g]).unwrap_or_default();
    if let Err(e) = client.register_worker(&worker_id, &hostname, &gpu_ids) {
        error!("Failed to register with server: {}", e);
        std::process::exit(1);
    }
    info!("Registered with server");

    // Main worker loop
    let result = worker_loop(
        &client,
        &worker_id,
        &runs_dir,
        &shutdown,
        Duration::from_secs(args.poll_interval),
        Duration::from_secs(args.heartbeat_interval),
        args.lease_seconds,
    );

    // Unregister on exit
    if let Err(e) = client.unregister_worker(&worker_id) {
        warn!("Failed to unregister: {}", e);
    }

    if let Err(e) = result {
        error!("Worker error: {}", e);
        std::process::exit(1);
    }

    info!("Worker stopped");
}

fn worker_loop(
    client: &WhirrClient,
    worker_id: &str,
    runs_dir: &PathBuf,
    shutdown: &Arc<AtomicBool>,
    poll_interval: Duration,
    heartbeat_interval: Duration,
    lease_seconds: u64,
) -> Result<(), Box<dyn std::error::Error>> {
    while !shutdown.load(Ordering::SeqCst) {
        // Try to claim a job
        let job = match client.claim_job(worker_id, lease_seconds) {
            Ok(job) => job,
            Err(e) => {
                warn!("Failed to claim job: {}", e);
                thread::sleep(poll_interval);
                continue;
            }
        };

        let job = match job {
            Some(j) => j,
            None => {
                // No jobs available
                thread::sleep(poll_interval);
                continue;
            }
        };

        let job_id = job.id;
        info!("Claimed job #{}: {}", job_id, job.name.as_deref().unwrap_or(&job.command_argv[0]));

        // Create run directory
        let run_id = format!("job-{}", job_id);
        let run_dir = runs_dir.join(&run_id);
        std::fs::create_dir_all(&run_dir)?;
        std::fs::create_dir_all(run_dir.join("artifacts"))?;

        // Start job
        let mut runner = JobRunner::new(
            job.command_argv.clone(),
            PathBuf::from(&job.workdir),
            run_dir.clone(),
            job_id,
            run_id.clone(),
        );

        if let Err(e) = runner.start() {
            error!("Failed to start job: {}", e);
            client.complete_job(job_id, worker_id, 1, Some(&run_id), Some(&e.to_string()))?;
            continue;
        }

        // Heartbeat loop while job is running
        let mut last_heartbeat = std::time::Instant::now();
        let mut cancel_requested = false;

        let exit_code = loop {
            // Check if job is done
            if let Some(code) = runner.try_wait()? {
                break code;
            }

            // Check for shutdown or cancellation
            if shutdown.load(Ordering::SeqCst) || cancel_requested {
                let reason = if shutdown.load(Ordering::SeqCst) { "shutdown" } else { "cancelled" };
                warn!("Killing job ({})...", reason);
                let code = runner.kill()?;
                break code;
            }

            // Send heartbeat if needed
            if last_heartbeat.elapsed() >= heartbeat_interval {
                match client.renew_lease(job_id, worker_id, lease_seconds) {
                    Ok(response) => {
                        if response.cancel_requested {
                            cancel_requested = true;
                        }
                    }
                    Err(e) => {
                        warn!("Heartbeat failed: {}", e);
                    }
                }
                last_heartbeat = std::time::Instant::now();
            }

            thread::sleep(Duration::from_millis(500));
        };

        // Report completion
        let error_message = if exit_code != 0 {
            Some(format!("Exit code: {}", exit_code))
        } else {
            None
        };

        if let Err(e) = client.complete_job(job_id, worker_id, exit_code, Some(&run_id), error_message.as_deref()) {
            warn!("Failed to report completion: {}", e);
        }

        if exit_code == 0 {
            info!("Job #{} completed", job_id);
        } else {
            warn!("Job #{} failed (exit code: {})", job_id, exit_code);
        }

        // Exit loop if shutdown requested
        if shutdown.load(Ordering::SeqCst) {
            break;
        }
    }

    Ok(())
}

fn gethostname() -> String {
    hostname::get()
        .map(|h| h.to_string_lossy().to_string())
        .unwrap_or_else(|_| "unknown".to_string())
}
