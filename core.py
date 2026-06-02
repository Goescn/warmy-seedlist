"""
Warmy Seed List — 核心业务逻辑

所有敏感凭据通过参数传入，不依赖环境变量。
API / CLI 调用方负责提供配置覆盖。
"""
import json, os, re, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formataddr
from email import encoders
from openpyxl import Workbook
from datetime import datetime

import requests


def sanitize_filename(name):
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
    return safe.strip('_')


# ─── Warmy API ─────────────────────────────────────────

def fetch_split_info(split_id, api_key, holder_uid):
    resp = requests.get(
        f"https://api.warmy.io/api/v2/users_splits/{split_id}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Holder-Uid": holder_uid,
            "Accept": "application/json"
        },
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def fetch_seedlist(split_id, api_key, holder_uid):
    """分页拉取种子列表，返回 (email_list, pagination_dict)"""
    emails = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        resp = requests.get(
            f"https://api.warmy.io/api/v2/users_splits/{split_id}/seedlist_emails",
            params={"page": page},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Holder-Uid": holder_uid,
                "Accept": "application/json"
            },
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", data.get("data", []))
        if not items:
            break
        emails.extend(items)
        pag = data.get("pagination", data.get("meta", {}))
        total_pages = pag.get("total_pages", 1)
        page += 1
    return emails, {"total_count": len(emails), "total_pages": total_pages}


# ─── Excel ─────────────────────────────────────────────

def create_excel(emails, sender_name, output_dir=None):
    """生成 Excel，返回 (count, filepath, filename)"""
    today = datetime.now().strftime('%Y%m%d')
    label = sanitize_filename(sender_name)
    filename = f"{label}_seedlist_{today}.xlsx"
    out = output_dir or os.path.expanduser("~/Desktop")
    filepath = os.path.join(out, filename)

    wb = Workbook()
    ws = wb.active
    ws.title = "Seed List Emails"
    ws["A1"] = "No."
    ws["B1"] = "Email Address"
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 50
    for i, email in enumerate(emails, 1):
        ws[f"A{i+1}"] = i
        ws[f"B{i+1}"] = email
    wb.save(filepath)
    return len(emails), filepath, filename


# ─── Feishu Drive ──────────────────────────────────────

def get_tenant_token(app_id, app_secret):
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10
    )
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 tenant token 失败: {data.get('msg')}")
    return data["tenant_access_token"]


def upload_file_to_drive(token, filepath, filename):
    with open(filepath, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/drive/v1/files/upload_all",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "file_name": filename,
                "parent_type": "explorer",
                "size": str(os.path.getsize(filepath))
            },
            files={"file": (filename, f,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30
        )
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"云盘上传失败: {data.get('msg')}")
    ft = data["data"]["file_token"]
    return ft, f"https://icnxvmdqjlv8.feishu.cn/file/{ft}"


def share_file_with_user(token, file_token, openid):
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/drive/v1/permissions/{file_token}/members",
        params={"type": "file", "need_notification": "false"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"member_type": "openid", "member_id": openid, "perm": "full_access"},
        timeout=10
    )
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"共享文件失败: {data.get('msg')}")


# ─── SMTP ──────────────────────────────────────────────

def send_email_smtp(filepath, filename, to_email, smtp_host, smtp_port, smtp_user, smtp_pass):
    if not to_email or not smtp_host or not smtp_user:
        return None
    msg = MIMEMultipart()
    msg["From"] = formataddr(("Warmy Seed List", smtp_user))
    msg["To"] = to_email
    msg["Subject"] = f"Warmy Seed List - {filename}"
    msg.attach(MIMEText(
        f"Warmy Seed List 自动生成\n文件: {filename}\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n请查收附件。",
        "plain", "utf-8"
    ))
    with open(filepath, "rb") as f:
        part = MIMEBase("application",
                        "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={filename}")
        msg.attach(part)
    with smtplib.SMTP_SSL(smtp_host, int(smtp_port), timeout=30) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    return to_email


# ─── Feishu Webhook Card ───────────────────────────────

def send_webhook_card(results, webhook_url):
    if not webhook_url:
        return None
    today = datetime.now().strftime('%Y-%m-%d')
    fields = []
    for r in results:
        fields.append({
            "is_short": True,
            "text": {
                "tag": "lark_md",
                "content": f"**{r['sender']}**\n📧 {r['count']} 个邮箱"
            }
        })
    elements = [{"tag": "div", "fields": fields}, {"tag": "hr"}]
    for i, r in enumerate(results, 1):
        line = f"**{i}. {r['sender']}**\n📧 {r['count']} 个邮箱\n"
        if r.get('url'):
            line += f"🔗 [下载 {r['filename']}]({r['url']})"
        if r.get('email_sent'):
            line += f"\n📬 已发送至 {r['email_sent']}"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text",
                      "content": f"⏰ {today} · Warmy Seed List"}]
    })
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 Warmy Seed List"},
            "template": "blue"
        },
        "elements": elements
    }
    resp = requests.post(webhook_url,
                         json={"msg_type": "interactive", "card": card},
                         timeout=15)
    return resp


# ─── 主执行函数 ────────────────────────────────────────

def run(senders, target_email=None, target_openid=None, output_dir=None,
        warmy_api_key=None, warmy_holder_uid=None,
        feishu_app_id=None, feishu_app_secret=None,
        feishu_webhook_url=None,
        smtp_host=None, smtp_port=None, smtp_user=None, smtp_pass=None):
    """
    完整执行流程: fetch → Excel → 上传飞书 → 发送邮件 → webhook 卡片

    参数:
      senders:         [{"split_id": str, "name": str}, ...]
      target_email:    收件邮箱（发附件）
      target_openid:   飞书文件分享目标用户
      warmy_api_key:   Warmy 凭证
      warmy_holder_uid: Warmy Holder UID
      feishu_app_id, feishu_app_secret:  飞书应用凭证
      feishu_webhook_url:                 飞书群机器人 webhook
      smtp_host/smtp_port/smtp_user/smtp_pass: SMTP 凭证

    返回: {"success": bool, "results": [...], "email_sent_to": str|None, "error": str|None}
    """
    if not warmy_api_key or not warmy_holder_uid:
        return {"success": False, "results": [], "error": "缺少 Warmy API 配置"}
    if not smtp_host or not smtp_user or not smtp_pass:
        return {"success": False, "results": [], "error": "缺少 SMTP 配置"}
    if not target_email:
        return {"success": False, "results": [], "error": "缺少目标邮箱"}
    if not feishu_app_id or not feishu_app_secret:
        return {"success": False, "results": [], "error": "缺少飞书应用凭证"}

    output_dir = output_dir or os.path.expanduser("~/Desktop")

    # ── Step 1: Fetch from Warmy ──
    results = []
    for s in senders:
        sid, sname = s["split_id"], s["name"]
        print(f"── {sname} (Split {sid}) ──")
        try:
            info = fetch_split_info(sid, warmy_api_key, warmy_holder_uid)
            print(f"  Info: {info.get('provider')}, {info.get('group_name')}")
        except Exception as e:
            print(f"  ❌ {e}")
            results.append({"sender": sname, "error": str(e), "count": 0})
            continue

        try:
            emails, pag = fetch_seedlist(sid, warmy_api_key, warmy_holder_uid)
            print(f"  ✅ {pag['total_count']} emails")
        except Exception as e:
            print(f"  ❌ {e}")
            results.append({"sender": sname, "error": str(e), "count": 0})
            continue

        try:
            cnt, fp, fn = create_excel(emails, sname, output_dir)
            print(f"  📝 {fn} — {cnt} emails")
        except Exception as e:
            print(f"  ❌ {e}")
            results.append({"sender": sname, "error": str(e), "count": 0})
            continue

        results.append({"sender": sname, "split_id": sid, "count": cnt,
                        "filepath": fp, "filename": fn, "emails": emails})

    if not any(r.get("count", 0) for r in results):
        return {"success": False, "results": results, "error": "没有成功拉取的数据"}

    # ── Step 2: Upload to Feishu Drive ──
    try:
        token = get_tenant_token(feishu_app_id, feishu_app_secret)
        for r in results:
            if r.get("count", 0) == 0:
                continue
            ft, url = upload_file_to_drive(token, r["filepath"], r["filename"])
            if target_openid:
                try:
                    share_file_with_user(token, ft, target_openid)
                except Exception:
                    pass
            r["url"] = url
    except Exception as e:
        print(f"  ⚠️ 飞书上传跳过: {e}")
        for r in results:
            r["url"] = None

    # ── Step 3: Send email via SMTP ──
    sent_to = None
    if target_email and smtp_host and smtp_user:
        for r in results:
            if r.get("count", 0) == 0:
                continue
            try:
                sent = send_email_smtp(r["filepath"], r["filename"],
                                       target_email,
                                       smtp_host, smtp_port, smtp_user, smtp_pass)
                r["email_sent"] = sent
                if sent:
                    sent_to = sent
            except Exception as e:
                print(f"  ❌ 邮件失败: {e}")

    # ── Step 4: Webhook card ──
    if feishu_webhook_url:
        try:
            send_webhook_card(results, feishu_webhook_url)
        except Exception as e:
            print(f"  ⚠️ 卡片跳过: {e}")

    return {"success": True, "results": results, "email_sent_to": sent_to}
