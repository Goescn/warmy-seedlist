"""
Warmy Seed List — FastAPI Web 服务
Railway 部署: uvicorn server:app --host 0.0.0.0 --port $PORT
"""
import json, os, sys, threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# 将项目根加入 sys.path，使 core.py 可导入
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from core import run as core_run

# ─── Paths ────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("WARMY_DATA_DIR", HERE / "data"))
LOG_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"
STATIC_DIR = HERE / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)


# ─── Default Config ───────────────────────────────────
DEFAULT_CONFIG = {
    "groups": [
        {"split_id": "19496", "name": "mosscode", "enabled": True},
        {"split_id": "20120", "name": "team.heynori.com", "enabled": True},
        {"split_id": "20119", "name": "news@send.lexar-store.com", "enabled": True},
    ],
    "target_email": "bing.liu@expertsender.cn",
    "target_openid": "ou_1a04df09dbec2e78b7d2c9fd82bab012",
    "schedule": {
        "enabled": True,
        "cron_expr": "0 12 * * 1",
        "timezone": "Asia/Shanghai",
        "label": "每周一 12:00",
    },
}


# ─── Config 持久化 ─────────────────────────────────────

def load_config():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ─── Pydantic Models ──────────────────────────────────

class Group(BaseModel):
    split_id: str
    name: str
    enabled: bool = True

class ScheduleConfig(BaseModel):
    enabled: bool
    cron_expr: str = "0 12 * * 1"
    timezone: str = "Asia/Shanghai"

class FetchRequest(BaseModel):
    group_id: Optional[str] = None
    target_email: Optional[str] = None
    target_openid: Optional[str] = None


# ─── App ──────────────────────────────────────────────

app = FastAPI(title="Warmy Seed List")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return idx.read_text()
    return "<h1>UI not found</h1>"


# ─── API ──────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    return load_config()


@app.post("/api/config/groups")
async def add_group(group: Group):
    cfg = load_config()
    cfg["groups"] = [g for g in cfg["groups"] if g["split_id"] != group.split_id]
    cfg["groups"].append(group.model_dump())
    save_config(cfg)
    return {"ok": True, "groups": cfg["groups"]}


@app.delete("/api/config/groups/{split_id}")
async def remove_group(split_id: str):
    cfg = load_config()
    cfg["groups"] = [g for g in cfg["groups"] if g["split_id"] != split_id]
    save_config(cfg)
    return {"ok": True, "groups": cfg["groups"]}


@app.post("/api/config/target-email")
async def set_target_email(data: dict):
    cfg = load_config()
    cfg["target_email"] = data.get("email", "")
    save_config(cfg)
    return {"ok": True, "email": cfg["target_email"]}


@app.post("/api/config/target-openid")
async def set_target_openid(data: dict):
    cfg = load_config()
    cfg["target_openid"] = data.get("openid", "")
    save_config(cfg)
    return {"ok": True, "openid": cfg["target_openid"]}


@app.post("/api/schedule")
async def update_schedule(data: ScheduleConfig):
    cfg = load_config()
    cfg["schedule"]["enabled"] = data.enabled
    cfg["schedule"]["cron_expr"] = data.cron_expr
    cfg["schedule"]["timezone"] = data.timezone
    cfg["schedule"]["label"] = f"Cron: {data.cron_expr}"
    save_config(cfg)
    return {"ok": True, "schedule": cfg["schedule"]}


@app.post("/api/fetch")
async def trigger_fetch(req: FetchRequest, background: BackgroundTasks):
    """触发 seedlist 拉取（后台异步执行）"""
    cfg = load_config()

    if req.group_id:
        groups = [{"split_id": req.group_id, "name": f"group_{req.group_id}"}]
    else:
        groups = [g for g in cfg["groups"] if g["enabled"]]

    if not groups:
        raise HTTPException(400, "没有可用的 group")

    target_email = req.target_email or cfg.get("target_email", "")
    target_openid = req.target_openid or cfg.get("target_openid", "")

    def _run():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_lines = [f"=== Warmy Fetch [{ts}] ==="]
        log_lines.append("GROUPS: " + json.dumps(groups, ensure_ascii=False))
        log_lines.append(f"TARGET_EMAIL: {target_email}")
        temp_dir = DATA_DIR / "tmp"
        temp_dir.mkdir(exist_ok=True)
        try:
            result = core_run(groups, target_email=target_email,
                              target_openid=target_openid,
                              output_dir=str(temp_dir))
            log_lines.append(json.dumps({"success": result["success"],
                                          "email_sent_to": result.get("email_sent_to"),
                                          "results_count": len(result.get("results", []))},
                                         ensure_ascii=False))
        except Exception as e:
            log_lines.append(f"ERROR: {e}")
        (LOG_DIR / f"run_{ts}.log").write_text("\n".join(log_lines))

    background.add_task(_run)
    return {
        "ok": True,
        "message": f"后台任务已启动 ({len(groups)} groups, 目标: {target_email})",
        "groups": groups,
    }


@app.get("/api/logs")
async def get_logs(limit: int = 20):
    logs = sorted(LOG_DIR.glob("run_*.log"), reverse=True)[:limit]
    return {"logs": [{"filename": lp.name, "timestamp": lp.stem.replace("run_", ""),
                      "preview": lp.read_text()[:500]} for lp in logs]}


@app.get("/api/logs/latest")
async def get_latest_log():
    logs = sorted(LOG_DIR.glob("run_*.log"), reverse=True)
    if not logs:
        return {"content": "暂无运行记录"}
    return {"content": logs[0].read_text(), "filename": logs[0].name}


@app.get("/api/health")
async def health():
    cfg = load_config()
    return {
        "status": "ok",
        "groups": len(cfg["groups"]),
        "schedule": cfg.get("schedule", {}),
        "data_dir": str(DATA_DIR),
    }
