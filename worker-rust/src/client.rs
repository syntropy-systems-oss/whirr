//! HTTP client for communicating with the whirr server.

use serde::{Deserialize, Serialize};
use std::time::Duration;

/// HTTP client for the whirr server API.
pub struct WhirrClient {
    base_url: String,
    agent: ureq::Agent,
}

/// Job data returned from the server.
#[derive(Debug, Deserialize)]
pub struct Job {
    pub id: i64,
    pub command_argv: Vec<String>,
    pub workdir: String,
    pub name: Option<String>,
    pub tags: Option<Vec<String>>,
}

/// Response from claim job endpoint.
#[derive(Debug, Deserialize)]
struct ClaimResponse {
    job: Option<Job>,
}

/// Response from heartbeat/lease renewal.
#[derive(Debug, Deserialize)]
pub struct HeartbeatResponse {
    #[serde(default)]
    pub cancel_requested: bool,
}

/// Worker registration request.
#[derive(Debug, Serialize)]
struct RegisterRequest<'a> {
    worker_id: &'a str,
    hostname: &'a str,
    gpu_ids: &'a [u32],
}

/// Job claim request.
#[derive(Debug, Serialize)]
struct ClaimRequest<'a> {
    worker_id: &'a str,
    lease_seconds: u64,
}

/// Lease renewal request.
#[derive(Debug, Serialize)]
struct RenewRequest<'a> {
    worker_id: &'a str,
    lease_seconds: u64,
}

/// Job completion request.
#[derive(Debug, Serialize)]
struct CompleteRequest<'a> {
    worker_id: &'a str,
    exit_code: i32,
    #[serde(skip_serializing_if = "Option::is_none")]
    run_id: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_message: Option<&'a str>,
}

impl WhirrClient {
    /// Create a new client connecting to the given server URL.
    pub fn new(base_url: &str) -> Self {
        let agent = ureq::AgentBuilder::new()
            .timeout_read(Duration::from_secs(30))
            .timeout_write(Duration::from_secs(30))
            .build();

        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            agent,
        }
    }

    /// Register this worker with the server.
    pub fn register_worker(
        &self,
        worker_id: &str,
        hostname: &str,
        gpu_ids: &[u32],
    ) -> Result<(), Box<dyn std::error::Error>> {
        let url = format!("{}/api/v1/workers/register", self.base_url);
        let request = RegisterRequest {
            worker_id,
            hostname,
            gpu_ids,
        };

        self.agent
            .post(&url)
            .send_json(&request)?;

        Ok(())
    }

    /// Unregister this worker from the server.
    pub fn unregister_worker(&self, worker_id: &str) -> Result<(), Box<dyn std::error::Error>> {
        let url = format!("{}/api/v1/workers/{}/unregister", self.base_url, worker_id);
        self.agent.post(&url).call()?;
        Ok(())
    }

    /// Try to claim the next available job.
    pub fn claim_job(
        &self,
        worker_id: &str,
        lease_seconds: u64,
    ) -> Result<Option<Job>, Box<dyn std::error::Error>> {
        let url = format!("{}/api/v1/jobs/claim", self.base_url);
        let request = ClaimRequest {
            worker_id,
            lease_seconds,
        };

        let response: ClaimResponse = self.agent
            .post(&url)
            .send_json(&request)?
            .into_json()?;

        Ok(response.job)
    }

    /// Renew the lease on a job (heartbeat).
    pub fn renew_lease(
        &self,
        job_id: i64,
        worker_id: &str,
        lease_seconds: u64,
    ) -> Result<HeartbeatResponse, Box<dyn std::error::Error>> {
        let url = format!("{}/api/v1/jobs/{}/heartbeat", self.base_url, job_id);
        let request = RenewRequest {
            worker_id,
            lease_seconds,
        };

        let response: HeartbeatResponse = self.agent
            .post(&url)
            .send_json(&request)?
            .into_json()?;

        Ok(response)
    }

    /// Report job completion.
    pub fn complete_job(
        &self,
        job_id: i64,
        worker_id: &str,
        exit_code: i32,
        run_id: Option<&str>,
        error_message: Option<&str>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let url = format!("{}/api/v1/jobs/{}/complete", self.base_url, job_id);
        let request = CompleteRequest {
            worker_id,
            exit_code,
            run_id,
            error_message,
        };

        self.agent
            .post(&url)
            .send_json(&request)?;

        Ok(())
    }
}
