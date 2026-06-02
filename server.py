"""
Warmy Seed List — FastAPI Web 服务（用户自助配置版）
所有凭据由用户在 UI 上自行填写，持久化在 config.json。
"""
import json, os, sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from core import run as core_run

# ─── Paths ────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("WARMY_DATA_DIR", HERE / "data"))
LOG_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"
STATIC_DIR = HERE / "static"
TMP_DIR = DATA_DIR / "tmp"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)


# ─── 空默认配置（用户自助填写） ─────────────────────────
DEFAULT_CONFIG = {
    "groups": [],
    "target_email": "",
    "warmy": {"api_key": "", "holder_uid": ""},
    "smtp": {"host": "", "port": 465, "user": "", "password": ""},
    "feishu": {"app_id": "", "app_secret": "", "webhook_url": ""},
    "schedule": {"enabled": False, "cron_expr": "0 12 * * 1", "timezone": "Asia/Shanghai"},
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

class GroupItem(BaseModel):
    split_id: str
    name: str
    enabled: bool = True

class WarmyConfig(BaseModel):
    api_key: str
    holder_uid: str

class SmtpConfig(BaseModel):
    host: str
    port: int = 465
    user: str
    password: str

class FeishuConfig(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    webhook_url: str = ""

class ScheduleConfig(BaseModel):
    enabled: bool = False
    cron_expr: str = "0 12 * * 1"
    timezone: str = "Asia/Shanghai"

class FetchRequest(BaseModel):
    group_id: Optional[str] = None
    target_email: Optional[str] = None


# ─── App ──────────────────────────────────────────────

app = FastAPI(title="Warmy Seed List")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── Pages ────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return idx.read_text()
    return "<h1>UI not found</h1>"


# ─── Config API ───────────────────────────────────────

@app.get("/api/config")
async def get_config():
    """返回完整配置（隐藏密码原文，只返回是否已设置）"""
    cfg = load_config()
    # 脱敏
    cfg.setdefault("smtp", {})
    cfg["smtp"] = {
        "host": cfg["smtp"].get("host", ""),
        "port": cfg["smtp"].get("port", 465),
        "user": cfg["smtp"].get("user", ""),
        "has_password": bool(cfg["smtp"].get("password")),
    }
    cfg.setdefault("warmy", {})
    cfg["warmy"] = {
        "has_api_key": bool(cfg["warmy"].get("api_key")),
        "has_holder_uid": bool(cfg["warmy"].get("holder_uid")),
    }
    cfg.setdefault("feishu", {})
    cfg["feishu"] = {
        "webhook_url": cfg["feishu"].get("webhook_url", ""),
        "has_app_id": bool(cfg["feishu"].get("app_id")),
        "has_app_secret": bool(cfg["feishu"].get("app_secret")),
    }
    return cfg


@app.get("/api/config/status")
async def config_status():
    """检查各模块是否已配置"""
    cfg = load_config()
    s = {
        "warmy_ready": bool(cfg.get("warmy", {}).get("api_key") and cfg["warmy"].get("holder_uid")),
        "smtp_ready": bool(cfg.get("smtp", {}).get("host") and cfg["smtp"].get("user") and cfg["smtp"].get("password")),
        "feishu_ready": bool(cfg.get("feishu", {}).get("app_id") and cfg["feishu"].get("app_secret")),
        "email_set": bool(cfg.get("target_email")),
        "groups_count": len(cfg.get("groups", [])),
    }
    s["ready"] = all([s["warmy_ready"], s["smtp_ready"], s["feishu_ready"], s["email_set"], s["groups_count"] > 0])
    return s


# ─── Warmy ────────────────────────────────────────────

@app.post("/api/config/warmy")
async def set_warmy(data: WarmyConfig):
    cfg = load_config()
    cfg["warmy"]["api_key"] = data.api_key
    cfg["warmy"]["holder_uid"] = data.holder_uid
    save_config(cfg)
    return {"ok": True}


# ─── SMTP ─────────────────────────────────────────────

@app.post("/api/config/smtp")
async def set_smtp(data: SmtpConfig):
    cfg = load_config()
    cfg["smtp"]["host"] = data.host
    cfg["smtp"]["port"] = data.port
    cfg["smtp"]["user"] = data.user
    cfg["smtp"]["password"] = data.password
    save_config(cfg)
    return {"ok": True}


# ─── Feishu ───────────────────────────────────────────

@app.post("/api/config/feishu")
async def set_feishu(data: FeishuConfig):
    cfg = load_config()
    cfg["feishu"]["app_id"] = data.app_id
    cfg["feishu"]["app_secret"] = data.app_secret
    cfg["feishu"]["webhook_url"] = data.webhook_url
    save_config(cfg)
    return {"ok": True}


# ─── Target Email ─────────────────────────────────────

@app.post("/api/config/target-email")
async def set_target_email(data: dict):
    cfg = load_config()
    cfg["target_email"] = data.get("email", "")
    save_config(cfg)
    return {"ok": True, "email": cfg["target_email"]}


# ─── Groups ───────────────────────────────────────────

@app.post("/api/config/groups")
async def add_group(group: GroupItem):
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


# ─── Schedule ─────────────────────────────────────────

@app.post("/api/config/schedule")
async def set_schedule(data: ScheduleConfig):
    cfg = load_config()
    cfg["schedule"]["enabled"] = data.enabled
    cfg["schedule"]["cron_expr"] = data.cron_expr
    cfg["schedule"]["timezone"] = data.timezone
    save_config(cfg)
    return {"ok": True, "schedule": cfg["schedule"]}


# ─── Fetch / Run ──────────────────────────────────────

@app.post("/api/fetch")
async def trigger_fetch(req: FetchRequest, background: BackgroundTasks):
    cfg = load_config()

    # 检查必要配置
    missing = []
    if not cfg.get("warmy", {}).get("api_key"): missing.append("Warmy API Key")
    if not cfg.get("smtp", {}).get("host"): missing.append("SMTP")
    if not cfg.get("feishu", {}).get("app_id"): missing.append("飞书应用")
    if not cfg.get("target_email"): missing.append("目标邮箱")
    if missing:
        raise HTTPException(400, f"缺少配置: {', '.join(missing)}")

    # 确定 groups
    if req.group_id:
        groups = [{"split_id": req.group_id, "name": f"group_{req.group_id}"}]
    else:
        groups = [g for g in cfg["groups"] if g.get("enabled", True)]
    if not groups:
        raise HTTPException(400, "没有可用的 Group (Split ID)")

    target_email = req.target_email or cfg.get("target_email", "")

    # 后台执行（用用户配置的凭据）
    def _run():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_lines = [f"=== Warmy Fetch [{ts}] ==="]
        log_lines.append("GROUPS: " + json.dumps(groups))
        log_lines.append(f"EMAIL: {target_email}")
        try:
            result = core_run(
                groups,
                target_email=target_email,
                output_dir=str(TMP_DIR),
                warmy_api_key=cfg["warmy"]["api_key"],
                warmy_holder_uid=cfg["warmy"]["holder_uid"],
                feishu_app_id=cfg["feishu"]["app_id"],
                feishu_app_secret=cfg["feishu"]["app_secret"],
                feishu_webhook_url=cfg["feishu"].get("webhook_url"),
                smtp_host=cfg["smtp"]["host"],
                smtp_port=cfg["smtp"]["port"],
                smtp_user=cfg["smtp"]["user"],
                smtp_pass=cfg["smtp"]["password"],
            )
            log_lines.append(json.dumps({"success": result["success"],
                                          "sent_to": result.get("email_sent_to"),
                                          "count": len(result.get("results", []))}))
        except Exception as e:
            log_lines.append(f"ERROR: {e}")
        (LOG_DIR / f"run_{ts}.log").write_text("\n".join(log_lines))

    background.add_task(_run)
    return {
        "ok": True,
        "message": f"任务已启动 ({len(groups)} groups, 邮件 → {target_email})",
    }


# ─── Logs ─────────────────────────────────────────────

@app.get("/api/logs/latest")
async def get_latest_log():
    logs = sorted(LOG_DIR.glob("run_*.log"), reverse=True)
    if not logs:
        return {"content": "暂无运行记录"}
    return {"content": logs[0].read_text()}


@app.get("/api/logs")
async def get_logs():
    logs = sorted(LOG_DIR.glob("run_*.log"), reverse=True)[:30]
    return [{"name": lp.name, "ts": lp.stem.replace("run_", ""),
             "size": lp.stat().st_size} for lp in logs]


# ─── Health ───────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}
