#!/usr/bin/env python3
import logging, os, queue, select, signal, sys, threading, time
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Config:
    psi_path: str = "/proc/pressure/memory"; psi_trigger: str = "some 150000 1000000"
    zram_dev: str = "zram0"; idle_mark_sec: float = 2.0; wb_interval_sec: float = 30.0; idle_age_sec: float = 60.0
    wb_burst: int = 1; poll_timeout_ms: int = 5000; metrics_path: Optional[str] = None; log_level: str = "INFO"; hot_reload_path: Optional[str] = None
    recomp_enable: bool = True; recomp_algo: str = "zstd"; recomp_max_pages: int = 4096
    @property
    def zram_sysfs(self) -> str: return f"/sys/block/{self.zram_dev}"
    @property
    def recomp_algo_file(self) -> str: return f"{self.zram_sysfs}/recomp_algorithm"
    @property
    def recomp_file(self) -> str: return f"{self.zram_sysfs}/recompress"
    @property
    def idle_file(self) -> str: return f"{self.zram_sysfs}/idle"
    @property
    def wb_file(self) -> str: return f"{self.zram_sysfs}/writeback"
    @classmethod
    def from_env(cls) -> "Config":
        d = cls()
        d.psi_path = os.environ.get("ZRAM_WB_PSI_PATH", d.psi_path); d.psi_trigger = os.environ.get("ZRAM_WB_PSI_TRIGGER", d.psi_trigger)
        d.zram_dev = os.environ.get("ZRAM_WB_ZRAM_DEV", d.zram_dev); d.idle_mark_sec = float(os.environ.get("ZRAM_WB_IDLE_MARK_SEC", d.idle_mark_sec))
        d.wb_interval_sec = float(os.environ.get("ZRAM_WB_INTERVAL_SEC", d.wb_interval_sec)); d.idle_age_sec = float(os.environ.get("ZRAM_WB_IDLE_AGE_SEC", d.idle_age_sec))
        d.wb_burst = int(os.environ.get("ZRAM_WB_BURST", d.wb_burst)); d.poll_timeout_ms = int(os.environ.get("ZRAM_WB_POLL_TIMEOUT_MS", d.poll_timeout_ms))
        d.metrics_path = os.environ.get("ZRAM_WB_METRICS_PATH", d.metrics_path); d.log_level = os.environ.get("ZRAM_WB_LOG_LEVEL", d.log_level)
        d.hot_reload_path = os.environ.get("ZRAM_WB_HOT_RELOAD_PATH", d.hot_reload_path)
        d.recomp_enable = os.environ.get("ZRAM_WB_RECOMP_ENABLE", "1") not in ("0", "false", "no"); d.recomp_algo = os.environ.get("ZRAM_WB_RECOMP_ALGO", d.recomp_algo)
        d.recomp_max_pages = int(os.environ.get("ZRAM_WB_RECOMP_MAX_PAGES", d.recomp_max_pages))
        return d

CFG = Config.from_env()
_LOG_FMT = "%(levelname)s [%(name)s] %(message)s" if "JOURNAL_STREAM" in os.environ else "%(asctime)s %(levelname)s [%(name)s] %(message)s"
logging.basicConfig(level=getattr(logging, CFG.log_level.upper(), logging.INFO), format=_LOG_FMT)
log = logging.getLogger("zram-wb")

@dataclass
class Metrics:
    psi_events: int = 0; writebacks_triggered: int = 0; writebacks_skipped_rate: int = 0
    writebacks_failed: int = 0; errors: int = 0; last_writeback_ts: float = 0.0
    last_cycle_pages_written: int = 0; max_cycle_pages_written: int = 0; cumulative_pages_written: int = 0
    recompress_triggered: int = 0; recompress_failed: int = 0; recompress_bytes_saved: int = 0
    start_ts: float = field(default_factory=time.monotonic)

    def to_prom(self) -> str:
        with METRICS_LOCK:
            now = time.monotonic(); uptime = now - self.start_ts
            lines = [
                "# HELP zram_wb_psi_events Total PSI memory events observed", "# TYPE zram_wb_psi_events counter", f"zram_wb_psi_events {self.psi_events}", "",
                "# HELP zram_wb_writebacks_total Total writeback attempts", "# TYPE zram_wb_writebacks_total counter", f"zram_wb_writebacks_total {self.writebacks_triggered}", "",
                "# HELP zram_wb_skipped_rate_limited Writebacks skipped due to rate-limit", "# TYPE zram_wb_skipped_rate_limited counter", f"zram_wb_skipped_rate_limited {self.writebacks_skipped_rate}", "",
                "# HELP zram_wb_failures Total writeback failures", "# TYPE zram_wb_failures counter", f"zram_wb_failures {self.writebacks_failed}", "",
                "# HELP zram_wb_errors_total Non-writeback errors (e.g. sysfs I/O)", "# TYPE zram_wb_errors_total counter", f"zram_wb_errors_total {self.errors}", "",
                "# HELP zram_wb_last_writeback_timestamp_seconds Unix epoch timestamp of last writeback", "# TYPE zram_wb_last_writeback_timestamp_seconds gauge", f"zram_wb_last_writeback_timestamp_seconds {self.last_writeback_ts:.1f}", "",
                "# HELP zram_wb_last_cycle_pages_written 4K pages moved by the most recent writeback call", "# TYPE zram_wb_last_cycle_pages_written gauge", f"zram_wb_last_cycle_pages_written {self.last_cycle_pages_written}", "",
                "# HELP zram_wb_max_cycle_pages_written Largest single-call page count observed since start", "# TYPE zram_wb_max_cycle_pages_written gauge", f"zram_wb_max_cycle_pages_written {self.max_cycle_pages_written}", "",
                "# HELP zram_wb_cumulative_pages_written Total 4K pages moved across all writeback calls since start", "# TYPE zram_wb_cumulative_pages_written counter", f"zram_wb_cumulative_pages_written {self.cumulative_pages_written}", "",
                "# HELP zram_wb_recompress_total Total recompress attempts", "# TYPE zram_wb_recompress_total counter", f"zram_wb_recompress_total {self.recompress_triggered}", "",
                "# HELP zram_wb_recompress_failures Total recompress failures", "# TYPE zram_wb_recompress_failures counter", f"zram_wb_recompress_failures {self.recompress_failed}", "",
                "# HELP zram_wb_recompress_bytes_saved Cumulative bytes freed in the zsmalloc pool by recompression", "# TYPE zram_wb_recompress_bytes_saved counter", f"zram_wb_recompress_bytes_saved {self.recompress_bytes_saved}", "",
                "# HELP zram_wb_uptime_seconds Daemon uptime", "# TYPE zram_wb_uptime_seconds gauge", f"zram_wb_uptime_seconds {uptime:.1f}", "",
            ]
        return "\n".join(lines)

METRICS = Metrics()
METRICS_LOCK = threading.Lock()

def _bump(attr: str) -> None:
    with METRICS_LOCK: setattr(METRICS, attr, getattr(METRICS, attr) + 1)

class TokenBucket:
    def __init__(self) -> None:
        self.tokens: float = float(CFG.wb_burst)
        self._lock = threading.Lock(); self._last = time.monotonic()

    def consume(self) -> bool:
        with self._lock:
            burst = CFG.wb_burst; rate = (1.0 / CFG.wb_interval_sec) if CFG.wb_interval_sec > 0 else 0.0
            now = time.monotonic(); elapsed = now - self._last
            self.tokens = min(burst, self.tokens + elapsed * rate); self._last = now
            if self.tokens >= 1.0: self.tokens -= 1.0; return True
            return False

def write_sysfs(path: str, val: str) -> bool:
    try:
        with open(path, "w") as fh: fh.write(val)
        return True
    except OSError as exc:
        log.error("write_sysfs(%r, %r): %s", path, val, exc); _bump("errors"); return False

def _bd_writes() -> int:
    try:
        with open(f"{CFG.zram_sysfs}/bd_stat") as fh: return int(fh.read().split()[2])
    except (OSError, IndexError, ValueError) as exc:
        log.error("_bd_writes(): could not read bd_stat: %s", exc); return -1

def _compr_size() -> int:
    try:
        with open(f"{CFG.zram_sysfs}/mm_stat") as fh: return int(fh.read().split()[1])
    except (OSError, IndexError, ValueError) as exc:
        log.error("_compr_size(): could not read mm_stat: %s", exc); return -1

_event_queue: queue.Queue[bool] = queue.Queue(maxsize=64)
_stop_event = threading.Event()
_idle_by_age = True
_wb_keyval = True
_recomp_available = True

def _mark_idle() -> bool:
    global _idle_by_age
    val = str(int(CFG.idle_age_sec)) if _idle_by_age else "all"
    if write_sysfs(CFG.idle_file, val): return True
    if _idle_by_age:
        _idle_by_age = False; log.warning("Age-based idle marking (needs CONFIG_ZRAM_TRACK_ENTRY_ACTIME) rejected by kernel; falling back to blanket 'all' for remaining runtime.")
        return write_sysfs(CFG.idle_file, "all")
    return False

def _register_recomp_algo() -> None:
    global _recomp_available
    if not CFG.recomp_enable: _recomp_available = False; return
    if write_sysfs(CFG.recomp_algo_file, f"algo={CFG.recomp_algo} priority=1"): log.info("Registered %s as priority-1 recompression algorithm on %s.", CFG.recomp_algo, CFG.zram_dev)
    else:
        _recomp_available = False; log.warning("recomp_algorithm rejected on %s (requires CONFIG_ZRAM_MULTI_COMP); recompression disabled for this run.", CFG.zram_dev)

def _trigger_recompress(type_: str) -> bool:
    global _recomp_available
    if not (CFG.recomp_enable and _recomp_available): return False
    before = _compr_size()
    val = f"type={type_} priority=1 max_pages={CFG.recomp_max_pages}" if CFG.recomp_max_pages > 0 else f"type={type_} priority=1"
    if not write_sysfs(CFG.recomp_file, val):
        _recomp_available = False; _bump("recompress_failed"); log.warning("recompress rejected on %s; disabling recompression for this run.", CFG.zram_dev); return False
    _bump("recompress_triggered")
    after = _compr_size()
    saved = (before - after) if (before >= 0 and after >= 0) else -1
    with METRICS_LOCK:
        if saved > 0: METRICS.recompress_bytes_saved += saved
    log.info("%s recompress freed %s bytes in zsmalloc pool on %s", type_, saved if saved >= 0 else "?", CFG.zram_dev)
    return True

def _trigger_writeback(wb_type: str) -> bool:
    global _wb_keyval
    before = _bd_writes()
    val = f"type={wb_type}" if _wb_keyval else wb_type
    ok = write_sysfs(CFG.wb_file, val)
    if not ok and _wb_keyval:
        _wb_keyval = False; log.warning("key=value writeback syntax rejected by kernel; falling back to legacy bare-word form.")
        ok = write_sysfs(CFG.wb_file, wb_type)
    if not ok:
        _bump("writebacks_failed"); return False
    _bump("writebacks_triggered")
    after = _bd_writes()
    moved = (after - before) if (before >= 0 and after >= 0) else -1
    with METRICS_LOCK:
        METRICS.last_writeback_ts = time.time()
        if moved >= 0:
            METRICS.last_cycle_pages_written = moved; METRICS.cumulative_pages_written += moved
            METRICS.max_cycle_pages_written = max(METRICS.max_cycle_pages_written, moved)
    log.info("%s writeback moved %s pages (%s KB) on %s", wb_type, moved if moved >= 0 else "?", moved * 4 if moved >= 0 else "?", CFG.zram_dev)
    return True

def _worker() -> None:
    limiter = TokenBucket()
    while not _stop_event.is_set():
        try: _event_queue.get(timeout=1.0)
        except queue.Empty: continue
        if _stop_event.is_set(): break

        if not limiter.consume():
            _bump("writebacks_skipped_rate"); log.info("Rate-limited: skipping writeback.")
            if CFG.metrics_path: _flush_metrics()
            continue

        log.info("Marking pages idle (age >= %s) on %s …", f"{int(CFG.idle_age_sec)}s" if _idle_by_age else "all", CFG.zram_dev)
        if not _mark_idle():
            _bump("writebacks_failed")
            if CFG.metrics_path: _flush_metrics()
            continue

        log.info("Waiting %.1f s for pages to settle …", CFG.idle_mark_sec)
        if _stop_event.wait(CFG.idle_mark_sec):
            log.info("Shutdown requested mid-settle; aborting this writeback cycle."); break

        log.info("Attempting idle recompression before writeback on %s …", CFG.zram_dev)
        _trigger_recompress("idle")

        log.info("Triggering idle+huge_idle writeback on %s …", CFG.zram_dev)
        _trigger_writeback("idle"); _trigger_writeback("huge_idle")

        if CFG.metrics_path: _flush_metrics()
    log.info("Worker thread exiting.")

def _flush_metrics() -> None:
    try:
        tmp = f"{CFG.metrics_path}.$$"
        with open(tmp, "w") as fh: fh.write(METRICS.to_prom())
        os.replace(tmp, CFG.metrics_path)
    except Exception as exc:
        log.warning("Failed to write metrics: %s", exc)

def _sigterm(_signum: int, _frame: object) -> None:
    _stop_event.set(); log.info("Received termination signal; shutting down.")

_reload_event = threading.Event()

def _sighup(_signum: int, _frame: object) -> None:
    _reload_event.set()

signal.signal(signal.SIGTERM, _sigterm); signal.signal(signal.SIGINT, _sigterm); signal.signal(signal.SIGHUP, _sighup)

def _apply_reload_file(cfg: "Config") -> Optional[str]:
    if not cfg.hot_reload_path or not os.path.exists(cfg.hot_reload_path): return None
    try:
        with open(cfg.hot_reload_path) as fh: lines = fh.read().splitlines()
    except OSError as exc:
        log.warning("Cannot read %s: %s", cfg.hot_reload_path, exc); return None
    new_trigger = None
    for line in lines:
        if "=" not in line: continue
        k, _, v = line.partition("="); k = k.strip(); v = v.strip()
        try:
            if k == "idle_age_sec": cfg.idle_age_sec = float(v)
            elif k == "wb_interval_sec": cfg.wb_interval_sec = float(v)
            elif k == "wb_burst": cfg.wb_burst = int(v)
            elif k == "idle_mark_sec": cfg.idle_mark_sec = float(v)
            elif k == "recomp_enable": cfg.recomp_enable = v not in ("0", "false", "no")
            elif k == "psi_trigger": new_trigger = v
            else: log.warning("Reload: unknown key %r ignored.", k)
        except ValueError as exc:
            log.warning("Reload: bad value for %s=%r ignored: %s", k, v, exc)
    return new_trigger

def _apply_reload(psi_fh, poller, psi_fd: int):
    if not CFG.hot_reload_path:
        log.warning("Reload requested but ZRAM_WB_HOT_RELOAD_PATH is not set; nothing to reload."); return psi_fh, psi_fd
    new_trigger = _apply_reload_file(CFG)
    log.info("Reloaded tunables from %s (idle_age_sec=%s wb_interval_sec=%s wb_burst=%s idle_mark_sec=%s)", CFG.hot_reload_path, CFG.idle_age_sec, CFG.wb_interval_sec, CFG.wb_burst, CFG.idle_mark_sec)

    if new_trigger and new_trigger != CFG.psi_trigger:
        try:
            new_fh = open(CFG.psi_path, "r+"); new_fh.write(new_trigger + "\n"); new_fh.flush()
        except OSError as exc:
            log.warning("Reload: new PSI trigger %r rejected by kernel, keeping old trigger %r: %s", new_trigger, CFG.psi_trigger, exc); return psi_fh, psi_fd
        poller.unregister(psi_fd)
        new_fd = new_fh.fileno(); poller.register(new_fd, select.POLLPRI)
        old_fh = psi_fh
        try: old_fh.close()
        except OSError: pass
        CFG.psi_trigger = new_trigger
        log.info("PSI trigger swapped live, no restart: %s", new_trigger)
        return new_fh, new_fd
    return psi_fh, psi_fd

def main() -> int:
    startup_trigger = _apply_reload_file(CFG)
    if startup_trigger: CFG.psi_trigger = startup_trigger
    if CFG.hot_reload_path and os.path.exists(CFG.hot_reload_path):
        log.info("Pre-seeded tunables applied at startup from %s (idle_age_sec=%s wb_interval_sec=%s wb_burst=%s idle_mark_sec=%s psi_trigger=%s)", CFG.hot_reload_path, CFG.idle_age_sec, CFG.wb_interval_sec, CFG.wb_burst, CFG.idle_mark_sec, CFG.psi_trigger)

    if not os.path.exists(CFG.psi_path):
        log.critical("PSI interface not found at %s. Is CONFIG_PSI enabled?", CFG.psi_path); return 1
    if not os.path.isdir(CFG.zram_sysfs):
        log.critical("ZRAM device %s not found at %s.", CFG.zram_dev, CFG.zram_sysfs); return 1
    _register_recomp_algo()

    try: psi_fh = open(CFG.psi_path, "r+")
    except OSError as exc:
        log.critical("Cannot open %s: %s", CFG.psi_path, exc); return 1

    try:
        psi_fh.write(CFG.psi_trigger + "\n"); psi_fh.flush()
    except OSError as exc:
        log.critical("Cannot write PSI trigger: %s", exc); return 1
    log.info("PSI trigger registered: %s", CFG.psi_trigger)

    wake_r, wake_w = os.pipe(); os.set_blocking(wake_w, False); signal.set_wakeup_fd(wake_w)
    psi_fd = psi_fh.fileno()

    worker = threading.Thread(target=_worker, name="wb-worker", daemon=False); worker.start()

    poller = select.poll(); poller.register(psi_fd, select.POLLPRI); poller.register(wake_r, select.POLLIN)

    while not _stop_event.is_set():
        try: evts = poller.poll(CFG.poll_timeout_ms)
        except (OSError, ValueError): break

        if _reload_event.is_set():
            _reload_event.clear(); psi_fh, psi_fd = _apply_reload(psi_fh, poller, psi_fd)

        for fd, flag in evts:
            if fd == wake_r:
                try: os.read(wake_r, 4096)
                except OSError: pass
                continue
            if fd != psi_fd or not (flag & select.POLLPRI): continue
            psi_fh.seek(0); line = psi_fh.readline()
            with METRICS_LOCK: METRICS.psi_events += 1
            log.debug("PSI event: %s", line.strip())
            try: _event_queue.put_nowait(True)
            except queue.Full: log.warning("Event queue full – dropping PSI event.")

    _stop_event.set()
    worker.join(timeout=CFG.wb_interval_sec + 10.0)
    if worker.is_alive():
        log.warning("Worker still mid-writeback after grace period; process will remain alive until it finishes (not force-killed).")
    worker.join()

    log.info("Flushing final metrics…"); _flush_metrics(); psi_fh.close()
    try: os.close(wake_r)
    except OSError: pass
    log.info("Exited cleanly.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
