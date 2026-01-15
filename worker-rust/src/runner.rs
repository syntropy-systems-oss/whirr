//! Job runner with process group management.
//!
//! Handles spawning jobs in separate process groups for clean termination,
//! capturing output to log files, and setting up the job environment.

use std::fs::File;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::thread;

use log::debug;

#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(unix)]
use nix::unistd::Pid;

/// Manages the execution of a single job.
pub struct JobRunner {
    command_argv: Vec<String>,
    workdir: PathBuf,
    run_dir: PathBuf,
    job_id: i64,
    run_id: String,
    child: Option<Child>,
    #[cfg(unix)]
    pgid: Option<i32>,
}

impl JobRunner {
    /// Create a new job runner.
    pub const fn new(
        command_argv: Vec<String>,
        workdir: PathBuf,
        run_dir: PathBuf,
        job_id: i64,
        run_id: String,
    ) -> Self {
        Self {
            command_argv,
            workdir,
            run_dir,
            job_id,
            run_id,
            child: None,
            #[cfg(unix)]
            pgid: None,
        }
    }

    /// Start the job process.
    pub fn start(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        if self.command_argv.is_empty() {
            return Err("Empty command".into());
        }

        let log_path = self.run_dir.join("output.log");
        let log_file = File::create(&log_path)?;
        let log_file_stderr = log_file.try_clone()?;

        debug!("Starting job in {workdir}", workdir = self.workdir.display());
        debug!("Command: {command_argv:?}", command_argv = &self.command_argv);
        debug!("Log file: {log_path}", log_path = log_path.display());

        let mut cmd = Command::new(&self.command_argv[0]);
        cmd.args(&self.command_argv[1..])
            .current_dir(&self.workdir)
            .stdout(Stdio::from(log_file))
            .stderr(Stdio::from(log_file_stderr))
            .env("WHIRR_JOB_ID", self.job_id.to_string())
            .env("WHIRR_RUN_DIR", self.run_dir.to_string_lossy().to_string())
            .env("WHIRR_RUN_ID", &self.run_id);

        // On Unix, create a new process group for clean termination.
        #[cfg(unix)]
        {
            cmd.process_group(0);
        }

        let child = cmd.spawn()?;

        #[cfg(unix)]
        {
            self.pgid = Some(i32::try_from(child.id())?);
        }

        self.child = Some(child);
        Ok(())
    }

    /// Check if the job has finished without blocking.
    /// Returns `Some(exit_code)` if finished, `None` if still running.
    pub fn try_wait(&mut self) -> Result<Option<i32>, Box<dyn std::error::Error>> {
        let child = self.child.as_mut().ok_or("Job not started")?;

        Ok(child
            .try_wait()?
            .map(|status| status.code().unwrap_or(-1)))
    }

    /// Kill the job and all its children.
    pub fn kill(&mut self) -> Result<i32, Box<dyn std::error::Error>> {
        #[cfg(unix)]
        {
            if let Some(pgid) = self.pgid {
                use nix::sys::signal::{killpg, Signal};

                // Send SIGTERM to process group
                debug!("Sending SIGTERM to process group {pgid}");
                let _ = killpg(Pid::from_raw(pgid), Signal::SIGTERM);

                // Wait a bit for graceful shutdown
                thread::sleep(std::time::Duration::from_secs(5));

                // Check if still running
                if let Some(child) = &mut self.child {
                    if let Some(status) = child.try_wait()? {
                        return Ok(status.code().unwrap_or(-1));
                    }
                    // Still running, send SIGKILL
                    debug!("Sending SIGKILL to process group {pgid}");
                    let _ = killpg(Pid::from_raw(pgid), Signal::SIGKILL);
                }
            }
        }

        // Fallback: kill the main process
        if let Some(child) = &mut self.child {
            let _ = child.kill();
            let status = child.wait()?;
            return Ok(status.code().unwrap_or(-1));
        }

        Ok(-1)
    }
}

impl Drop for JobRunner {
    fn drop(&mut self) {
        // Ensure we clean up the process on drop
        if self.child.is_some() {
            let _ = self.kill();
        }
    }
}
