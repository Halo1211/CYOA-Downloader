//! Background download worker: a dedicated thread owning a Tokio runtime.
//!
//! The UI never blocks:
//! - Jobs are submitted through a bounded channel (`try_send`; a full channel is
//!   reported to the caller, never waited on).
//! - Cancellation and shutdown do NOT travel through that channel. They act on
//!   shared [`Control`] state directly from the UI thread, so they cannot be lost
//!   when the job channel is full.
//! - Events come back through a bounded channel that the UI drains with
//!   `try_recv`. Progress is lossy (`try_send`); `Started`/`Finished` are always
//!   delivered.
//! - Jobs run one at a time, like the Python queue.

use cyoa_engine::{
    pipeline::Progress,
    run::{RunError, RunOptions, run_download},
};
use std::{
    collections::{HashMap, VecDeque},
    sync::{
        Arc, Mutex, PoisonError,
        mpsc::{Receiver, SyncSender, TrySendError, sync_channel},
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};
use tokio::sync::{Notify, mpsc};
use tokio_util::sync::CancellationToken;

pub type JobId = u64;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JobSummary {
    pub written: Vec<String>,
    pub failed_assets: usize,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JobResult {
    Done(JobSummary),
    Failed(String),
    Cancelled,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Started {
        job: JobId,
    },
    Progress {
        job: JobId,
        done: usize,
        total: usize,
        name: String,
        ok: bool,
    },
    Finished {
        job: JobId,
        result: JobResult,
    },
}

/// Wakes the UI after an event is queued (egui `request_repaint` in the app).
pub type Waker = Box<dyn Fn() + Send + Sync>;

/// Shared cancellation state; every method is non-blocking for the UI.
#[derive(Default)]
struct Control {
    tokens: Mutex<HashMap<JobId, CancellationToken>>,
    shutdown: CancellationToken,
    /// Wakes the worker loop so cancelled queued jobs are finished promptly.
    changed: Notify,
    /// Python `_paused`: no new job starts while set (the running one finishes).
    paused: std::sync::atomic::AtomicBool,
}

impl Control {
    fn token(&self, job: JobId) -> CancellationToken {
        self.tokens
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .entry(job)
            .or_default()
            .clone()
    }
    fn forget(&self, job: JobId) {
        self.tokens
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .remove(&job);
    }
    #[cfg(test)]
    fn live_tokens(&self) -> usize {
        self.tokens
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .len()
    }
}

pub struct WorkerHandle {
    jobs: mpsc::Sender<(JobId, JobSpec)>,
    events: Receiver<Event>,
    control: Arc<Control>,
    thread: Option<JoinHandle<()>>,
}

const JOB_CHANNEL_CAPACITY: usize = 1024;
const EVENT_CAPACITY: usize = 256;
/// Upper bound on jobs waiting inside the worker (beyond the channel).
pub const MAX_PENDING_JOBS: usize = 10_000;

impl WorkerHandle {
    pub fn spawn(waker: Waker) -> std::io::Result<Self> {
        let (job_tx, job_rx) = mpsc::channel(JOB_CHANNEL_CAPACITY);
        let (ev_tx, ev_rx) = sync_channel(EVENT_CAPACITY);
        let control = Arc::new(Control::default());
        let worker_control = control.clone();
        let thread = std::thread::Builder::new()
            .name("cyoa-worker".into())
            .spawn(move || {
                let runtime = match tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(3)
                    .enable_all()
                    .build()
                {
                    Ok(rt) => rt,
                    Err(e) => {
                        eprintln!("worker runtime failed: {e}");
                        return;
                    }
                };
                let link = Link {
                    events: ev_tx,
                    waker,
                };
                runtime.block_on(worker_loop(job_rx, Arc::new(link), worker_control));
                runtime.shutdown_timeout(Duration::from_secs(2));
            })?;
        Ok(Self {
            jobs: job_tx,
            events: ev_rx,
            control,
            thread: Some(thread),
        })
    }

    /// Queue a job without blocking. Returns false if the worker is gone or full.
    pub fn start(&self, job: JobId, opts: impl Into<JobSpec>) -> bool {
        let opts = opts.into();
        // Register the token first so a Cancel issued right away is never lost.
        let _ = self.control.token(job);
        let ok = self.jobs.try_send((job, opts)).is_ok();
        if !ok {
            self.control.forget(job);
        }
        ok
    }

    /// Never lost, never blocks.
    pub fn cancel(&self, job: JobId) {
        if let Some(t) = self
            .control
            .tokens
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(&job)
        {
            t.cancel();
        }
        self.control.changed.notify_one();
    }

    /// Python `_toggle_pause`: gate the next job start.
    pub fn set_paused(&self, paused: bool) {
        self.control
            .paused
            .store(paused, std::sync::atomic::Ordering::SeqCst);
        self.control.changed.notify_one();
    }

    pub fn cancel_all(&self) {
        self.control
            .tokens
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .values()
            .for_each(CancellationToken::cancel);
        self.control.changed.notify_one();
    }

    pub fn try_recv(&self) -> Option<Event> {
        self.events.try_recv().ok()
    }

    /// Signal shutdown (non-blocking). Running work observes cancellation.
    pub fn begin_shutdown(&self) {
        self.cancel_all();
        self.control.shutdown.cancel();
        self.control.changed.notify_one();
    }

    /// Signal shutdown and wait at most `timeout` for the worker thread.
    pub fn shutdown(&mut self, timeout: Duration) {
        self.begin_shutdown();
        let deadline = Instant::now() + timeout;
        while self.thread.as_ref().is_some_and(|t| !t.is_finished()) && Instant::now() < deadline {
            // Drain (bounded) so a pending lifecycle send cannot block the worker.
            for _ in 0..EVENT_CAPACITY {
                if self.events.try_recv().is_err() {
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        if let Some(t) = self.thread.take_if(|t| t.is_finished()) {
            let _ = t.join();
        }
    }

    #[cfg(test)]
    fn live_tokens(&self) -> usize {
        self.control.live_tokens()
    }
}

impl Drop for WorkerHandle {
    fn drop(&mut self) {
        self.shutdown(Duration::from_millis(500));
    }
}

struct Link {
    events: SyncSender<Event>,
    waker: Waker,
}

impl Link {
    /// Lossy delivery for progress; wakes the UI only if something was queued.
    fn progress(&self, ev: Event) -> bool {
        match self.events.try_send(ev) {
            Ok(()) => {
                (self.waker)();
                true
            }
            Err(TrySendError::Full(_)) => true,
            Err(TrySendError::Disconnected(_)) => false,
        }
    }

    /// Reliable delivery (blocking thread) for lifecycle events.
    async fn lifecycle(self: &Arc<Self>, ev: Event) {
        let link = self.clone();
        let _ = tokio::task::spawn_blocking(move || {
            if link.events.send(ev).is_ok() {
                (link.waker)();
            }
        })
        .await;
    }
}

async fn worker_loop(
    mut jobs: mpsc::Receiver<(JobId, JobSpec)>,
    link: Arc<Link>,
    control: Arc<Control>,
) {
    let mut queue: VecDeque<(JobId, JobSpec)> = VecDeque::new();
    let mut running: Option<(JobId, tokio::task::JoinHandle<()>)> = None;

    loop {
        // Finish cancelled queued jobs immediately, independent of the running job.
        let mut cancelled = Vec::new();
        queue.retain(|(id, _)| {
            let c = control.token(*id).is_cancelled();
            if c {
                cancelled.push(*id);
            }
            !c
        });
        for id in cancelled {
            control.forget(id);
            link.lifecycle(Event::Finished {
                job: id,
                result: JobResult::Cancelled,
            })
            .await;
        }

        if control.shutdown.is_cancelled() {
            if let Some((id, mut h)) = running.take() {
                if tokio::time::timeout(Duration::from_secs(2), &mut h)
                    .await
                    .is_err()
                {
                    h.abort();
                }
                control.forget(id);
            }
            return;
        }

        if running.is_none()
            && !control.paused.load(std::sync::atomic::Ordering::SeqCst)
            && let Some((id, opts)) = queue.pop_front()
        {
            let token = control.token(id);
            running = Some((id, tokio::spawn(run_job(id, opts, token, link.clone()))));
        }

        tokio::select! {
            joined = async {
                match running.as_mut() {
                    Some((_, h)) => h.await,
                    None => std::future::pending().await,
                }
            }, if running.is_some() => {
                if let Some((id, _)) = running.take() {
                    control.forget(id);
                    if let Err(e) = joined {
                        // Panic/abort after Started: never leave the UI showing Running.
                        link.lifecycle(Event::Finished {
                            job: id,
                            result: JobResult::Failed(format!("internal error: {e}")),
                        })
                        .await;
                    }
                }
            }
            job = jobs.recv() => match job {
                Some((id, opts)) => {
                    if queue.len() >= MAX_PENDING_JOBS {
                        control.forget(id);
                        link.lifecycle(Event::Finished {
                            job: id,
                            result: JobResult::Failed("queue is full".into()),
                        })
                        .await;
                    } else {
                        queue.push_back((id, opts));
                    }
                }
                None => control.shutdown.cancel(),
            },
            () = control.changed.notified() => {}
            () = control.shutdown.cancelled() => {}
        }
    }
}

/// A queued download plus GUI-level post-processing.
pub struct JobSpec {
    pub opts: RunOptions,
    /// Register the result in this CYOA Manager library (Python GUI toggle).
    pub manager_db: Option<std::path::PathBuf>,
    /// "Auto (detect)": resolve the concrete mode first (`auto_detect_output`).
    pub auto: Option<&'static str>,
}

impl From<RunOptions> for JobSpec {
    fn from(opts: RunOptions) -> Self {
        Self {
            opts,
            manager_db: None,
            auto: None,
        }
    }
}

/// Python `_detect_auto` before the job, then the concrete mode's options.
async fn resolve_auto(opts: &mut RunOptions, pref: &str, cancel: &CancellationToken) {
    use cyoa_engine::run::{Engine, OutputMode};
    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: url::Url::parse(&opts.url).ok(),
            allow_internal: cyoa_core::allow_internal_hosts(),
        },
        proxy: opts.proxy.clone(),
        dns: opts.dns.clone(),
        vpn: opts.vpn.clone(),
        ..Default::default()
    });
    let mode = match client {
        Ok(c) => {
            let d = cyoa_engine::auto_detect::detect_auto(&c, &opts.url, pref, cancel).await;
            opts.url = d.effective_url;
            d.mode
        }
        Err(e) => {
            eprintln!("[Auto-detect] Error for {}: {e}", opts.url);
            cyoa_engine::auto_detect::output_variant("website", pref)
        }
    };
    let (out, engine) = match mode {
        "website_zip" => (OutputMode::IccZip, Engine::Standard),
        "cyoap_vue_zip" => (OutputMode::IccZip, Engine::CyoapVue),
        "cyoap_vue_folder" => (OutputMode::IccFolder, Engine::CyoapVue),
        _ => (OutputMode::IccFolder, Engine::Standard),
    };
    opts.mode = out;
    opts.engine = engine;
}

async fn run_job(job: JobId, spec: JobSpec, cancel: CancellationToken, link: Arc<Link>) {
    let JobSpec {
        mut opts,
        manager_db,
        auto,
    } = spec;
    link.lifecycle(Event::Started { job }).await;
    if let Some(pref) = auto {
        resolve_auto(&mut opts, pref, &cancel).await;
    }
    // Python GUI: setup_file_logging(outdir) when a download starts (off the UI thread).
    let dir = opts.output_dir.clone();
    let _ =
        tokio::task::spawn_blocking(move || cyoa_engine::logfile::setup_file_logging(&dir)).await;

    let (ptx, mut prx) = mpsc::channel::<Progress>(64);
    let fwd = link.clone();
    let forward = tokio::spawn(async move {
        while let Some(p) = prx.recv().await {
            if let Progress::AssetDone {
                done,
                total,
                ok,
                name,
            } = p
            {
                fwd.progress(Event::Progress {
                    job,
                    done,
                    total,
                    name,
                    ok,
                });
            }
        }
    });
    // Like the CLI: with the itch.io downloader enabled, the optional itch-dl
    // backend runs first for itch.io URLs; it never affects the CYOA result.
    let itch_url = cyoa_engine::itch::is_itch_url(&opts.url);
    let itch_only = opts.integrations.itch_only && itch_url;
    let mut itch_outcome: Option<Result<String, String>> = None;
    if (opts.integrations.itch || itch_only) && itch_url {
        tracing::info!("itch.io downloader enabled — invoking itch-dl backend.");
        let (url, out, cancel_c) = (opts.url.clone(), opts.output_dir.clone(), cancel.clone());
        // itch-only mirrors web builds like the CLI's `--itch-only`.
        let mirror = opts.integrations.itch_mirror_web || itch_only;
        let parallel = opts.integrations.itch_parallel.clamp(1, 16);
        let html5 = cyoa_engine::offline_viewer::NetworkAssetFetcher::new(
            &url,
            opts.proxy.clone(),
            opts.dns.clone(),
            opts.vpn.clone(),
            opts.http2,
            cancel_c.clone(),
        );
        match tokio::task::spawn_blocking(move || {
            cyoa_engine::itch::download_itch_assets(
                &url,
                &out,
                None,
                mirror,
                parallel,
                &cancel_c,
                &cyoa_engine::itch::SystemItchBackend,
                html5
                    .as_ref()
                    .map(|f| f as &dyn cyoa_engine::offline_viewer::AssetFetcher),
            )
        })
        .await
        {
            Ok(Ok(r)) => {
                tracing::info!("[itch] {}", r.message);
                itch_outcome = Some(if r.ok { Ok(r.message) } else { Err(r.message) });
            }
            Ok(Err(e)) => {
                tracing::warn!("[itch] downloader error (CYOA result unaffected): {e}");
                itch_outcome = Some(Err(e.to_string()));
            }
            Err(e) => {
                tracing::warn!("[itch] Optional backend failed: {e}");
                itch_outcome = Some(Err(e.to_string()));
            }
        }
    }
    let result = if itch_only {
        // itch.io only: the CYOA download is skipped; the job reflects itch-dl.
        drop(ptx);
        match itch_outcome {
            Some(Ok(_)) => Ok(cyoa_engine::run::RunReport {
                stem: "itch_assets".into(),
                written_paths: vec![opts.output_dir.join("itch_assets")],
                failed_assets: Vec::new(),
            }),
            Some(Err(e)) => Err(cyoa_engine::run::RunError::OutputStage(format!(
                "itch.io: {e}"
            ))),
            None => Err(cyoa_engine::run::RunError::OutputStage(
                "itch.io: backend did not run".into(),
            )),
        }
    } else {
        run_download(&opts, cancel.clone(), Some(ptx)).await
    };
    let _ = forward.await;
    let result = match result {
        Ok(report) => {
            if let Some(db) = manager_db {
                let embedded = !matches!(opts.mode, cyoa_engine::run::OutputMode::Zip);
                let (written, name, url) = (
                    report.written_paths.clone(),
                    report.stem.clone(),
                    opts.url.clone(),
                );
                let _ = tokio::task::spawn_blocking(move || {
                    cyoa_integrations::cyoa_manager::register_output(
                        &db, embedded, &written, &name, &url,
                    )
                })
                .await;
            }
            JobResult::Done(JobSummary {
                written: report
                    .written_paths
                    .iter()
                    .map(|p| p.display().to_string())
                    .collect(),
                failed_assets: report.failed_assets.len(),
            })
        }
        Err(RunError::Cancelled) => JobResult::Cancelled,
        Err(_) if cancel.is_cancelled() => JobResult::Cancelled,
        Err(e) => JobResult::Failed(e.to_string()),
    };
    link.lifecycle(Event::Finished { job, result }).await;
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn wait_finished(w: &WorkerHandle, job: JobId, within: Duration) -> Option<JobResult> {
        let end = Instant::now() + within;
        while Instant::now() < end {
            while let Some(ev) = w.try_recv() {
                if let Event::Finished { job: j, result } = ev
                    && j == job
                {
                    return Some(result);
                }
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        None
    }

    /// Unroutable TEST-NET address: hangs on connect until cancelled.
    fn hanging() -> RunOptions {
        RunOptions {
            url: "http://192.0.2.1/project.json".into(),
            output_dir: std::env::temp_dir(),
            cache_dir: None,
            ..RunOptions::default()
        }
    }

    #[test]
    fn invalid_url_job_fails_without_blocking() {
        let mut w = WorkerHandle::spawn(Box::new(|| {})).unwrap();
        let opts = RunOptions {
            url: "not-a-url".into(),
            output_dir: std::env::temp_dir(),
            cache_dir: None,
            ..RunOptions::default()
        };
        assert!(w.start(1, opts));
        let r = wait_finished(&w, 1, Duration::from_secs(10)).unwrap();
        assert!(matches!(r, JobResult::Failed(_)), "{r:?}");
        w.shutdown(Duration::from_secs(3));
    }

    #[test]
    fn cancelling_queued_job_finishes_it_while_another_runs() {
        let mut w = WorkerHandle::spawn(Box::new(|| {})).unwrap();
        assert!(w.start(1, hanging()));
        assert!(w.start(2, hanging()));
        std::thread::sleep(Duration::from_millis(200));
        w.cancel(2);
        // Job 2 finishes even though job 1 is still running.
        assert_eq!(
            wait_finished(&w, 2, Duration::from_secs(3)),
            Some(JobResult::Cancelled)
        );
        w.cancel(1);
        assert!(wait_finished(&w, 1, Duration::from_secs(5)).is_some());
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(w.live_tokens(), 0, "tokens of finished jobs are dropped");
        w.shutdown(Duration::from_secs(3));
    }

    #[test]
    fn cancel_all_and_shutdown_are_prompt() {
        let mut w = WorkerHandle::spawn(Box::new(|| {})).unwrap();
        for id in 1..=5 {
            assert!(w.start(id, hanging()));
        }
        std::thread::sleep(Duration::from_millis(200));
        let t = Instant::now();
        w.cancel_all();
        let mut finished = std::collections::HashSet::new();
        let end = Instant::now() + Duration::from_secs(5);
        while finished.len() < 5 && Instant::now() < end {
            while let Some(ev) = w.try_recv() {
                if let Event::Finished { job, .. } = ev {
                    finished.insert(job);
                }
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(finished.len(), 5, "{finished:?}");
        w.shutdown(Duration::from_secs(3));
        assert!(w.thread.is_none(), "worker thread joined");
        assert!(t.elapsed() < Duration::from_secs(6));
    }

    /// Python `_paused`: no queued job starts while paused; Continue resumes.
    #[test]
    fn pause_holds_queued_jobs_until_continue() {
        let mut w = WorkerHandle::spawn(Box::new(|| {})).unwrap();
        w.set_paused(true);
        for id in 1..=2 {
            assert!(w.start(id, hanging()));
        }
        std::thread::sleep(Duration::from_millis(300));
        let mut started = Vec::new();
        while let Some(ev) = w.try_recv() {
            if let Event::Started { job } = ev {
                started.push(job);
            }
        }
        assert!(started.is_empty(), "started while paused: {started:?}");
        w.set_paused(false);
        let end = Instant::now() + Duration::from_secs(5);
        while started.is_empty() && Instant::now() < end {
            while let Some(ev) = w.try_recv() {
                if let Event::Started { job } = ev {
                    started.push(job);
                }
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(started, vec![1]);
        w.cancel_all();
        w.shutdown(Duration::from_secs(3));
    }

    /// Minimal static file server for `fixtures/e2e_site` (one thread).
    fn serve_fixture_site() -> std::net::SocketAddr {
        use std::io::{Read, Write};
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/e2e_site");
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                let mut req = [0u8; 4096];
                let n = stream.read(&mut req).unwrap_or(0);
                let line = String::from_utf8_lossy(&req[..n]).into_owned();
                let path = line.split_whitespace().nth(1).unwrap_or("/");
                let file = root.join(path.trim_start_matches('/'));
                let (status, body) = match std::fs::read(&file) {
                    Ok(b) if !path.contains("..") => ("200 OK", b),
                    _ => ("404 Not Found", b"not found".to_vec()),
                };
                let ctype = if path.ends_with(".json") {
                    "application/json"
                } else if path.ends_with(".png") {
                    "image/png"
                } else if path.ends_with(".jpg") {
                    "image/jpeg"
                } else {
                    "text/plain"
                };
                let head = format!(
                    "HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&body);
            }
        });
        addr
    }

    #[test]
    fn gui_worker_downloads_fixture_project_end_to_end() {
        let addr = serve_fixture_site();
        let out = tempfile::tempdir().unwrap();
        let progress = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let wake = progress.clone();
        let mut w = WorkerHandle::spawn(Box::new(move || {
            wake.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        }))
        .unwrap();
        let opts = RunOptions {
            url: format!("http://{addr}/mycyoa/project.json"),
            output_dir: out.path().to_path_buf(),
            mode: cyoa_engine::run::OutputMode::Both,
            wait_time: 0,
            cache_dir: None,
            ..RunOptions::default()
        };
        assert!(w.start(7, opts));
        let mut saw_progress = false;
        let mut result = None;
        let end = Instant::now() + Duration::from_secs(60);
        while result.is_none() && Instant::now() < end {
            while let Some(ev) = w.try_recv() {
                match ev {
                    Event::Progress { job: 7, .. } => saw_progress = true,
                    Event::Finished { job: 7, result: r } => result = Some(r),
                    _ => {}
                }
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        match result {
            Some(JobResult::Done(s)) => {
                assert_eq!(s.failed_assets, 1, "img/missing.png is a 404");
                assert!(
                    s.written
                        .iter()
                        .any(|p| p.ends_with("mycyoa_project.json.json"))
                );
                assert!(
                    s.written
                        .iter()
                        .any(|p| p.ends_with("mycyoa_project.json.zip"))
                );
            }
            other => panic!("unexpected result: {other:?}"),
        }
        assert!(saw_progress, "progress events reached the UI channel");
        assert!(
            progress.load(std::sync::atomic::Ordering::Relaxed) > 0,
            "UI was woken"
        );
        let embedded =
            std::fs::read_to_string(out.path().join("mycyoa_project.json.json")).unwrap();
        assert!(embedded.contains("data:image/png;base64,"));
        w.shutdown(Duration::from_secs(3));
    }
}
