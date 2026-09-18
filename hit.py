import telebot
import requests
import json
import base64
import random
import time
import threading
import websocket
import ssl
import os
from io import BytesIO
from flask import Flask

# =========================
# THÔNG TIN BOT
# =========================
TOKEN = os.environ.get("BOT_TOKEN", "8385677064:AAEtgKFsUGflb5a5H5F93xELObm5zoNfxTc")
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

user_data = {}
auto_data = {}

# =========================
# ADMIN CONFIG
# =========================
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "7564889663").split(",") if x.strip()]
admin_broadcast_state = {}

WS_URL = "wss://mynisketgw.hytsocesk.com/websocket"

# =========================
# CẤU HÌNH NGUỒN DỰ ĐOÁN (ẨN)
# =========================
_SRC_BASE = "https://api.tool247.fun/api/pred-log"
_SRC_GAME = "hitclub_tx"
_SRC_LIMIT = 1

# =========================
# CẤU HÌNH MARTINGALE
# =========================
MAX_LOSE_STREAK = 2

# =========================
# FLASK KEEP-ALIVE
# =========================
app = Flask(__name__)


@app.route("/")
def _health_root():
    return "OK - AI CORE v7.0 running", 200


@app.route("/health")
def _health():
    return {
        "status": "alive",
        "time": int(time.time()),
        "users": len(user_data),
        "auto_running": sum(1 for d in auto_data.values() if d.get("running"))
    }, 200


def _run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


threading.Thread(target=_run_flask, daemon=True).start()

tx_history = []
history_lock = threading.Lock()
_pred_cache = {"prediction": None, "confidence": 0, "cau": "", "phien": None, "ts": 0}


def is_admin(chat_id):
    return chat_id in ADMIN_IDS


def admin_only(func):
    def wrapper(message):
        if not is_admin(message.chat.id):
            bot.reply_to(message, "⛔ <b>BẠN KHÔNG CÓ QUYỀN ADMIN.</b>")
            return
        return func(message)
    return wrapper


# =========================
# FETCH NGUỒN DỰ ĐOÁN (ẨN)
# =========================
def _fetch_prediction():
    try:
        url = f"{_SRC_BASE}?game={_SRC_GAME}&limit={_SRC_LIMIT}&t={int(time.time()*1000)}"
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        lich_su = data.get("lich_su") or []
        if not lich_su:
            return None
        e0 = lich_su[0]
        raw = str(e0.get("du_doan", "")).strip().lower()
        if "tài" in raw or raw == "t":
            pred = "Tài"
        elif "xỉu" in raw or raw == "x":
            pred = "Xỉu"
        else:
            return None
        return {
            "prediction": pred,
            "confidence": int(e0.get("do_tin_cay", 0)),
            "cau": e0.get("loai_cau", ""),
            "phien": e0.get("phien"),
            "ket_qua": e0.get("ket_qua", ""),
            "ket_luan": e0.get("ket_luan", ""),
            "tong": data.get("tong", 0),
            "chinh_xac": data.get("chinh_xac", "0%"),
            "ts": time.time()
        }
    except Exception:
        return None


def add_to_history(session, result, total):
    with history_lock:
        if any(h["session"] == session for h in tx_history):
            return
        tx_history.append({"session": session, "result": result, "totalScore": total})
        if len(tx_history) > 1000:
            tx_history.pop(0)


def generate_prediction():
    result = _fetch_prediction()
    if result:
        _pred_cache.update(result)
        print(f"[CORE] Phiên #{result['phien']} | Dự đoán: {result['prediction']} "
              f"| Độ tin cậy: {result['confidence']}% | Cầu: {result['cau']}")
        return result["prediction"]

    with history_lock:
        if tx_history:
            last = tx_history[-1]["result"]
            return "Xỉu" if last == "Tài" else "Tài"
    return random.choice(["Tài", "Xỉu"])


# =========================
# CAPTCHA & LOGIN
# =========================
def gen_fg_id():
    return ''.join(random.choices('0123456789abcdef', k=32))


def get_captcha(fg_id):
    url = "https://bodergatez.dsrcgoms.net/verify/index.aspx"
    headers = {
        "Accept": "*/*", "Content-Type": "text/plain;charset=UTF-8",
        "Origin": "https://v.hitclub.life", "Referer": "https://v.hitclub.life/",
        "User-Agent": "Mozilla/5.0", "X-Fg-Id": fg_id
    }
    try:
        r = requests.post(url, headers=headers, json={"fg_id": ""}, timeout=10)
        data = r.json()
        if data.get("auth") == 1:
            return base64.b64decode(data["c"]["b64"]), data["c"]["token"], data["c"]["message"]
    except Exception:
        pass
    return None, None, None


def do_login(username, password, captcha, fg_id, captcha_token):
    url = "https://bodergatez.dsrcgoms.net/user/login.aspx"
    current_time = int(time.time())
    payload = {
        "username": username, "password": password, "app_id": "bc114103",
        "os": "Windows", "device": "Computer", "browser": "chrome",
        "fg": fg_id, "time": current_time, "sign": "3e9068479bef99977957f8874b15e64d",
        "version": "3.12.2", "bunleid": "", "csrf": "", "r_token": "",
        "token": captcha_token, "captcha": captcha, "aff_id": "hitclub",
        "d": {"k": "a5875b05b9", "e": "", "t": current_time}
    }
    headers = {
        "Accept": "*/*", "Content-Type": "application/json",
        "Origin": "https://v.hitclub.life", "Referer": "https://v.hitclub.life/",
        "User-Agent": "Mozilla/5.0", "X-Fg-Id": fg_id
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


# =========================
# AUTO BET
# =========================
def start_auto(chat_id):
    cfg = user_data[chat_id]
    access_token = cfg["access_token"]

    if chat_id not in auto_data or not auto_data[chat_id].get("running"):
        auto_data[chat_id] = {
            "running": True, "connected": False, "poller_active": False,
            "last_sid": 0, "balance": cfg["balance"], "target": cfg["target"],
            "bet_amount": cfg["bet_amount"], "base_bet": cfg["bet_amount"],
            "lose_streak": 0, "waiting_recover": False,
            "total_bet_count": 0,
            "total_win_count": 0, "total_lose_count": 0,
            "waiting_result": False, "last_predict": None
        }
    else:
        auto_data[chat_id]["running"] = True

    def ws_history_poller(ws):
        while auto_data[chat_id].get("running") and auto_data[chat_id].get("poller_active"):
            try:
                ws.send(json.dumps(["6", "MiniGame", "taixiuPlugin", {"cmd": 1005}]))
            except:
                pass
            time.sleep(3)

    def on_open(ws):
        auto_data[chat_id]["connected"] = True
        ws.send(json.dumps([1, "MiniGame", "", "",
                            {"agentId": "1", "accessToken": access_token, "reconnect": False}]))
        bot.send_message(chat_id,
            "✅ <b>KẾT NỐI MÁY CHỦ THÀNH CÔNG</b>\n"
            "🔄 <i>Đang xác thực tài khoản...</i>\n"
            f"🎲 <b>Vốn gốc:</b> {auto_data[chat_id]['base_bet']:,.0f}đ\n"
            f"📈 <b>Chế độ:</b> x2 khi thua | dừng ở tay {MAX_LOSE_STREAK} | reset khi thắng"
        )

    def process_result(ws, msg_sid, d1, d2, d3):
        if not auto_data[chat_id]["waiting_result"] or msg_sid != auto_data[chat_id]["last_sid"]:
            return
        tong = d1 + d2 + d3
        ket_qua = "Tài" if tong > 10 else "Xỉu"
        xuc_xac = f"{d1}-{d2}-{d3}"
        old_predict = auto_data[chat_id]["last_predict"]
        bet_amount = auto_data[chat_id]["bet_amount"]
        auto_data[chat_id]["waiting_result"] = False

        # === CHẾ ĐỘ CHỜ HỒI ===
        if auto_data[chat_id].get("waiting_recover"):
            if old_predict == ket_qua:
                auto_data[chat_id]["waiting_recover"] = False
                auto_data[chat_id]["lose_streak"] = 0
                auto_data[chat_id]["bet_amount"] = auto_data[chat_id]["base_bet"]
                bot.send_message(
                    chat_id,
                    f"🎯 <b>PHIÊN HỒI THÀNH CÔNG — {msg_sid}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎲 <b>Xúc xắc:</b> {xuc_xac} <i>(Tổng {tong})</i>\n"
                    f"🎯 <b>Kết quả:</b> {ket_qua.upper()}\n"
                    f"🔮 <b>AI đã dự:</b> {old_predict.upper()} ✅\n"
                    f"💰 <b>Reset vốn cược:</b> {auto_data[chat_id]['base_bet']:,.0f}đ\n"
                    f"🚀 <b>Sẽ cược lại từ phiên tiếp theo!</b>"
                )
            else:
                bot.send_message(
                    chat_id,
                    f"⏳ <b>PHIÊN {msg_sid} CHƯA HỒI</b>\n"
                    f"🎲 {xuc_xac} — <b>{ket_qua.upper()}</b> | AI dự: {old_predict.upper()} ❌\n"
                    f"<i>Tiếp tục chờ...</i>"
                )
            return

        # === CƯỢC BÌNH THƯỜNG ===
        auto_data[chat_id]["total_bet_count"] += 1

        if ket_qua == old_predict:
            profit = int(bet_amount * 0.97)
            auto_data[chat_id]["balance"] += (bet_amount + profit)
            auto_data[chat_id]["total_win_count"] += 1
            msg_title = f"🎉 <b>CHIẾN THẮNG PHIÊN {msg_sid}!</b> 🎉"
            tien_thay_doi = f"📈 <b>Tiền lãi:</b> +{profit:,.0f}đ"
            auto_data[chat_id]["bet_amount"] = auto_data[chat_id]["base_bet"]
            auto_data[chat_id]["lose_streak"] = 0
        else:
            auto_data[chat_id]["total_lose_count"] += 1
            msg_title = f"💀 <b>THUA CƯỢC PHIÊN {msg_sid}</b> 💀"
            tien_thay_doi = f"📉 <b>Lỗ:</b> -{bet_amount:,.0f}đ"
            auto_data[chat_id]["lose_streak"] += 1
            if auto_data[chat_id]["lose_streak"] >= MAX_LOSE_STREAK:
                auto_data[chat_id]["waiting_recover"] = True
                auto_data[chat_id]["bet_amount"] = auto_data[chat_id]["base_bet"]
            else:
                auto_data[chat_id]["bet_amount"] *= 2

        bot.send_message(
            chat_id,
            f"{msg_title}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎲 <b>Xúc xắc:</b> {xuc_xac} <i>(Tổng {tong})</i>\n"
            f"🎯 <b>Kết quả:</b> {ket_qua.upper()}\n"
            f"👉 <b>Bạn cược:</b> {old_predict.upper()}\n"
            f"💰 <b>Tiền cược:</b> {bet_amount:,.0f}đ\n"
            f"{tien_thay_doi}\n"
            f"💵 <b>Số dư mới:</b> {auto_data[chat_id]['balance']:,.0f}đ\n"
            f"📊 <b>Chuỗi thua:</b> {auto_data[chat_id]['lose_streak']}/{MAX_LOSE_STREAK}\n"
            f"🎲 <b>Vốn phiên tới:</b> {auto_data[chat_id]['bet_amount']:,.0f}đ\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

        if auto_data[chat_id]["waiting_recover"]:
            bot.send_message(
                chat_id,
                f"🛑 <b>GÃY {MAX_LOSE_STREAK} TAY — TẠM DỪNG CƯỢC</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 <b>Số dư:</b> {auto_data[chat_id]['balance']:,.0f}đ\n"
                f"⏳ <b>Đang chờ 1 phiên AI dự đúng để cược lại từ vốn gốc...</b>"
            )

        if not auto_data[chat_id]["waiting_recover"]:
            if auto_data[chat_id]["balance"] < auto_data[chat_id]["bet_amount"]:
                bot.send_message(
                    chat_id,
                    f"⛔ <b>KHÔNG THỂ CƯỢC TIẾP!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 <b>Số dư hiện tại:</b> {auto_data[chat_id]['balance']:,.0f}đ\n"
                    f"🎲 <b>Tiền cược yêu cầu:</b> {auto_data[chat_id]['bet_amount']:,.0f}đ\n"
                    f"❌ <b>Lý do:</b> Số dư không đủ để vào phiên tiếp theo.\n"
                    f"🛑 <b>Đã tự động dừng Bot.</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                auto_data[chat_id]["running"] = False
                if chat_id in auto_data:
                    user_data[chat_id]["balance"] = auto_data[chat_id]["balance"]
                ws.close()
                return

        if auto_data[chat_id]["balance"] >= auto_data[chat_id]["target"]:
            bot.send_message(chat_id, f"🏆 <b>ĐẠT MỤC TIÊU LỢI NHUẬN!</b>\n💰 <b>Số dư cuối:</b> {auto_data[chat_id]['balance']:,.0f}đ")
            auto_data[chat_id]["running"] = False
            ws.close()
        elif auto_data[chat_id]["balance"] <= 0:
            bot.send_message(chat_id, f"💔 <b>HẾT SỐ DƯ!</b>\n💰 <b>Số dư cuối:</b> {auto_data[chat_id]['balance']:,.0f}đ")
            auto_data[chat_id]["running"] = False
            ws.close()

    def on_message(ws, message):
        try:
            if not auto_data[chat_id].get("running"):
                ws.close(); return
            data = json.loads(message)
            if not isinstance(data, list):
                return
            if len(data) >= 5 and data[0] == 1 and data[1] is True:
                bot.send_message(chat_id, "🔓 <b>XÁC THỰC THÀNH CÔNG</b>\n📡 <i>Đang vào bàn và đợi kết quả...</i>")
                if not auto_data[chat_id].get("poller_active"):
                    auto_data[chat_id]["poller_active"] = True
                    threading.Thread(target=ws_history_poller, args=(ws,), daemon=True).start()
                return
            if data[0] != 5: return
            msg = data[1]
            if not isinstance(msg, dict): return
            cmd = msg.get("cmd")

            if cmd == 1005:
                hst = msg.get("hst", [])
                if hst:
                    for item in hst:
                        s_id = item.get("s"); dice_str = item.get("d")
                        if s_id and dice_str and len(dice_str) == 3:
                            d1, d2, d3 = int(dice_str[0]), int(dice_str[1]), int(dice_str[2])
                            tong = d1 + d2 + d3
                            kq = "Tài" if tong > 10 else "Xỉu"
                            add_to_history(s_id, kq, tong)
                    if auto_data[chat_id]["waiting_result"]:
                        target_sid = auto_data[chat_id]["last_sid"]
                        for item in reversed(hst):
                            if item.get("s") == target_sid:
                                dice_str = item.get("d")
                                if dice_str and len(dice_str) == 3:
                                    d1, d2, d3 = int(dice_str[0]), int(dice_str[1]), int(dice_str[2])
                                    process_result(ws, target_sid, d1, d2, d3)
                                break

            elif cmd == 1008:
                sid = msg.get("sid")
                if not sid or sid == auto_data[chat_id]["last_sid"]: return
                if auto_data[chat_id]["waiting_result"]: return

                if auto_data[chat_id].get("waiting_recover"):
                    du_doan = generate_prediction()
                    auto_data[chat_id]["last_sid"] = sid
                    auto_data[chat_id]["last_predict"] = du_doan
                    auto_data[chat_id]["waiting_result"] = True
                    bot.send_message(
                        chat_id,
                        f"👀 <b>ĐANG CHỜ HỒI — KHÔNG CƯỢC PHIÊN {sid}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔮 <b>Nếu cược, AI sẽ chọn:</b> {du_doan.upper()}\n"
                        f"⏳ <i>Chờ kết quả để xác định phiên hồi...</i>"
                    )
                    return

                amount = auto_data[chat_id]["bet_amount"]

                if auto_data[chat_id]["balance"] < amount:
                    bot.send_message(
                        chat_id,
                        f"⛔ <b>KHÔNG THỂ VÀO TIỀN PHIÊN {sid}!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💵 <b>Số dư hiện tại:</b> {auto_data[chat_id]['balance']:,.0f}đ\n"
                        f"🎲 <b>Tiền cược yêu cầu:</b> {amount:,.0f}đ\n"
                        f"❌ <b>Lý do:</b> Số dư không đủ.\n"
                        f"🛑 <b>Đã tự động dừng Bot.</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    auto_data[chat_id]["running"] = False
                    if chat_id in auto_data:
                        user_data[chat_id]["balance"] = auto_data[chat_id]["balance"]
                    ws.close()
                    return

                du_doan = generate_prediction()
                eid = 1 if du_doan == "Tài" else 2

                auto_data[chat_id]["balance"] -= amount
                ws.send(json.dumps(["6", "MiniGame", "taixiuPlugin",
                                    {"cmd": 1000, "b": amount, "sid": sid, "aid": 1,
                                     "eid": eid, "sqe": True, "a": False}]))
                auto_data[chat_id]["last_sid"] = sid
                auto_data[chat_id]["last_predict"] = du_doan
                auto_data[chat_id]["waiting_result"] = True

                streak_now = auto_data[chat_id]["lose_streak"]
                bot.send_message(
                    chat_id,
                    f"🚀 <b>ĐÃ VÀO TIỀN PHIÊN {sid}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔮 <b>AI Dự đoán:</b> {du_doan.upper()}\n"
                    f"💰 <b>Vốn cược:</b> {amount:,.0f}đ\n"
                    f"📊 <b>Tay thứ:</b> {streak_now + 1}/{MAX_LOSE_STREAK}\n"
                    f"💵 <b>Số dư sau cược:</b> {auto_data[chat_id]['balance']:,.0f}đ\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>Đang chờ Game trả kết quả... ⏳</i>"
                )

            elif cmd in [1003, 1004]:
                if auto_data[chat_id]["waiting_result"]:
                    d1, d2, d3 = msg.get("d1"), msg.get("d2"), msg.get("d3")
                    cd = msg.get("cd", [])
                    if isinstance(cd, list) and len(cd) == 3:
                        d1, d2, d3 = cd[0], cd[1], cd[2]
                    if d1 is not None and d2 is not None and d3 is not None:
                        s_id = msg.get("sid", auto_data[chat_id]["last_sid"])
                        process_result(ws, s_id, d1, d2, d3)
        except Exception:
            pass

    def on_error(ws, error):
        print(f"[WS LỖI] {str(error)[:100]}")

    def on_close(ws, close_status_code, close_msg):
        auto_data[chat_id]["connected"] = False
        auto_data[chat_id]["poller_active"] = False

    headers = [
        "Origin: https://v.hitclub.life",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer: https://v.hitclub.life/"
    ]

    while auto_data[chat_id].get("running"):
        ws = websocket.WebSocketApp(WS_URL, header=headers, on_open=on_open,
                                    on_message=on_message, on_error=on_error, on_close=on_close)
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        if auto_data[chat_id].get("running"):
            bot.send_message(chat_id, "🔌 <b>MẤT KẾT NỐI. ĐANG TỰ ĐỘNG KẾT NỐI LẠI SAU 5 GIÂY...</b>")
            time.sleep(5)
        else:
            bot.send_message(chat_id, "🔌 <b>ĐÃ DỪNG TIẾN TRÌNH AUTO.</b>")
            if chat_id in auto_data:
                user_data[chat_id]["balance"] = auto_data[chat_id]["balance"]


# =========================
# LỆNH USER
# =========================
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "🌟 <b>HỆ THỐNG AUTO CƯỢC AI</b> 🌟\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✨ <b>/login</b> - Đăng nhập tài khoản\n"
        "⚡ <b>/auto</b> - Bắt đầu treo máy cược\n"
        "🛑 <b>/stopbet</b> - Dừng cược an toàn\n"
        "💳 <b>/balance</b> - Kiểm tra số dư\n"
        "📊 <b>/status</b> - Xem thống kê Win/Lose\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎲 <b>Cơ chế cược:</b>\n"
        "• X2 khi thua\n"
        "• Gãy 2 tay → tạm dừng, chờ phiên AI dự đúng\n"
        "• Thắng → quay về vốn gốc\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Phiên bản: 7.0 - Martingale V3</i>"
    )


@bot.message_handler(commands=['balance'])
def show_balance(message):
    chat_id = message.chat.id
    if chat_id in auto_data:
        data = auto_data[chat_id]
        bot.reply_to(message,
            f"💳 <b>THÔNG TIN VÍ</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Số dư thực tế:</b> {data['balance']:,.0f}đ\n"
            f"🎯 <b>Mục tiêu chốt lời:</b> {data['target']:,.0f}đ\n"
            f"📈 <b>Cần kiếm thêm:</b> {data['target'] - data['balance']:,.0f}đ\n"
            f"🎲 <b>Vốn gốc:</b> {data['base_bet']:,.0f}đ\n"
            f"🎲 <b>Vốn phiên tới:</b> {data['bet_amount']:,.0f}đ\n"
            f"📊 <b>Chuỗi thua:</b> {data['lose_streak']}/{MAX_LOSE_STREAK}\n"
            f"⏳ <b>Chờ hồi:</b> {'Có' if data.get('waiting_recover') else 'Không'}\n"
            f"━━━━━━━━━━━━━━━━━━━━")
    elif chat_id in user_data and "balance" in user_data[chat_id]:
        bot.reply_to(message, f"💳 <b>Số dư hiện tại:</b> {user_data[chat_id]['balance']:,.0f}đ")
    else:
        bot.reply_to(message, "⚠️ <b>CHƯA ĐĂNG NHẬP!</b>")


@bot.message_handler(commands=['status'])
def status(message):
    chat_id = message.chat.id
    if chat_id in auto_data:
        data = auto_data[chat_id]
        win_rate = (data['total_win_count'] / data['total_bet_count'] * 100) if data['total_bet_count'] > 0 else 0
        status_text = "🟢 ĐANG HOẠT ĐỘNG" if data.get("running") else "🔴 ĐÃ DỪNG"
        recover_text = "⏳ ĐANG CHỜ HỒI" if data.get("waiting_recover") else "🎲 ĐANG CƯỢC"
        bot.reply_to(message,
            f"📊 <b>BẢNG THỐNG KÊ AUTO</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 <b>Trạng thái:</b> {status_text}\n"
            f"🎯 <b>Chế độ:</b> {recover_text}\n"
            f"💵 <b>Số dư ví:</b> {data['balance']:,.0f}đ\n"
            f"🎲 <b>Vốn gốc:</b> {data['base_bet']:,.0f}đ\n"
            f"🎲 <b>Vốn phiên tới:</b> {data['bet_amount']:,.0f}đ\n"
            f"📊 <b>Chuỗi thua:</b> {data['lose_streak']}/{MAX_LOSE_STREAK}\n"
            f"🎰 <b>Tổng ván cược:</b> {data['total_bet_count']}\n"
            f"✅ <b>Thắng:</b> {data['total_win_count']} | ❌ <b>Thua:</b> {data['total_lose_count']}\n"
            f"🔥 <b>Win Rate:</b> {win_rate:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━")
    else:
        bot.reply_to(message, "⚠️ <b>CHƯA CÓ DỮ LIỆU!</b>")


@bot.message_handler(commands=['login'])
def login(message):
    user_data[message.chat.id] = {"step": "login"}
    bot.reply_to(message, "🔐 <b>YÊU CẦU ĐĂNG NHẬP</b>\n👉 <code>tên_đăng_nhập|mật_khẩu</code>")


@bot.message_handler(func=lambda m: user_data.get(m.chat.id, {}).get("step") == "login")
def userpass(message):
    chat_id = message.chat.id
    try:
        username, password = message.text.split("|")
        fg_id = gen_fg_id()
        img_data, captcha_token, captcha_msg = get_captcha(fg_id)
        if not img_data:
            bot.reply_to(message, "❌ <b>Lỗi tải Captcha!</b>\nVui lòng thử lại /login")
            return
        user_data[chat_id] = {"step": "captcha", "username": username.strip(),
                              "password": password.strip(), "fg_id": fg_id, "captcha_token": captcha_token}
        bot.send_photo(chat_id, BytesIO(img_data), caption=f"🛡 <b>{captcha_msg}</b>")
    except:
        bot.reply_to(message, "⚠️ <b>SAI ĐỊNH DẠNG!</b>\nVD: user|pass")


@bot.message_handler(func=lambda m: user_data.get(m.chat.id, {}).get("step") == "captcha")
def captcha_login(message):
    chat_id = message.chat.id
    cfg = user_data[chat_id]
    result = do_login(cfg["username"], cfg["password"], message.text.strip(), cfg["fg_id"], cfg["captcha_token"])
    if result.get("status") != "OK":
        bot.reply_to(message, f"❌ <b>ĐĂNG NHẬP THẤT BẠI</b>\nLý do: {result.get('message', 'Lỗi')}\nVui lòng /login lại.")
        return
    info = result["data"][0]
    user_data[chat_id] = {"step": "config", "access_token": info["token"], "balance": info["main_balance"]}
    bot.reply_to(message,
        f"✅ <b>ĐĂNG NHẬP THÀNH CÔNG</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Tài khoản:</b> {info['username']}\n"
        f"💵 <b>Số dư hiện có:</b> {info['main_balance']:,.0f}đ\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚙️ <b>CẤU HÌNH THÔNG SỐ AUTO</b>\n"
        f"👉 <code>số_dư|mục_tiêu|tiền_cược</code>\n"
        f"<i>VD: {info['main_balance']}|2000000|1000</i>\n"
        f"🎲 <b>Lưu ý:</b> Bot tự động x2 khi thua, dừng ở tay {MAX_LOSE_STREAK}, reset khi thắng.")


@bot.message_handler(func=lambda m: user_data.get(m.chat.id, {}).get("step") == "config")
def config(message):
    chat_id = message.chat.id
    try:
        parts = message.text.split("|")
        balance_int, target_int, bet_int = int(parts[0]), int(parts[1]), int(parts[2])
        if bet_int < 1000:
            raise Exception("Vốn cược tối thiểu 1,000đ")
        if target_int <= balance_int:
            raise Exception("Mục tiêu chốt lời phải lớn hơn số dư")
        if balance_int < bet_int:
            raise Exception("Số dư nhỏ hơn tiền cược, không thể bắt đầu")
        user_data[chat_id].update({"balance": balance_int, "target": target_int,
                                   "bet_amount": bet_int, "step": "ready"})
        bot.reply_to(message,
            f"✅ <b>ĐÃ LƯU CẤU HÌNH</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Số dư:</b> {balance_int:,.0f}đ\n"
            f"🎯 <b>Mục tiêu:</b> {target_int:,.0f}đ\n"
            f"🎲 <b>Vốn gốc:</b> {bet_int:,.0f}đ\n"
            f"📈 <b>Cơ chế:</b> x2 khi thua | dừng tay {MAX_LOSE_STREAK} | reset khi thắng\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 <i>Sẵn sàng! Hãy gõ /auto để kích hoạt.</i>")
    except Exception as e:
        bot.reply_to(message, f"⚠️ <b>LỖI CẤU HÌNH:</b> {str(e)}")


@bot.message_handler(commands=['auto'])
def auto(message):
    chat_id = message.chat.id
    if chat_id not in user_data or user_data[chat_id].get("step") != "ready":
        bot.reply_to(message, "⚠️ <b>CHƯA HOÀN TẤT CẤU HÌNH!</b>")
        return
    if chat_id in auto_data and auto_data[chat_id].get("running"):
        bot.reply_to(message, "⏳ <b>ĐANG HOẠT ĐỘNG RỒI!</b>")
        return

    bal = user_data[chat_id].get("balance", 0)
    bet = user_data[chat_id].get("bet_amount", 0)
    if bal < bet:
        bot.reply_to(
            message,
            f"⛔ <b>KHÔNG THỂ BẮT ĐẦU AUTO!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Số dư hiện tại:</b> {bal:,.0f}đ\n"
            f"🎲 <b>Tiền cược yêu cầu:</b> {bet:,.0f}đ\n"
            f"❌ <b>Lý do:</b> Số dư không đủ để vào phiên đầu tiên.\n"
            f"👉 Vui lòng nạp thêm tiền hoặc giảm tiền cược rồi /login lại.\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        return

    if chat_id in auto_data:
        user_data[chat_id]["balance"] = auto_data[chat_id]["balance"]
    threading.Thread(target=start_auto, args=(chat_id,), daemon=True).start()
    bot.reply_to(message,
        "🚀 <b>KHỞI ĐỘNG AI CORE THÀNH CÔNG</b> 🚀\n"
        f"📈 <i>Chế độ: x2 khi thua | dừng tay {MAX_LOSE_STREAK} | reset khi thắng</i>"
    )


@bot.message_handler(commands=['stopbet'])
def stopbet(message):
    chat_id = message.chat.id
    if chat_id in auto_data and auto_data[chat_id].get("running"):
        auto_data[chat_id]["running"] = False
        bot.reply_to(message, "🛑 <b>ĐÃ TIẾP NHẬN LỆNH DỪNG AUTO</b> 🛑")
    else:
        bot.reply_to(message, "⚠️ <b>KHÔNG CÓ TIẾN TRÌNH NÀO ĐANG CHẠY.</b>")


# =========================
# LỆNH ADMIN
# =========================
@bot.message_handler(commands=['admin'])
@admin_only
def admin_panel(message):
    total_users = len(user_data)
    total_running = sum(1 for d in auto_data.values() if d.get("running"))
    total_balance = sum(d.get("balance", 0) for d in auto_data.values())
    bot.reply_to(message,
        f"👑 <b>ADMIN PANEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Tổng user đã login:</b> {total_users}\n"
        f"🟢 <b>Đang auto:</b> {total_running}\n"
        f"💰 <b>Tổng balance auto:</b> {total_balance:,.0f}đ\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>LỆNH ADMIN:</b>\n"
        f"<code>/users</code> - Danh sách user\n"
        f"<code>/userinfo &lt;chat_id&gt;</code> - Xem chi tiết user\n"
        f"<code>/stopall</code> - Dừng toàn bộ auto\n"
        f"<code>/broadcast &lt;nội_dung&gt;</code> - Gửi thông báo\n"
        f"<code>/setrole &lt;chat_id&gt;</code> - Thêm admin\n"
        f"<code>/unsetrole &lt;chat_id&gt;</code> - Xoá admin\n"
        f"<code>/addbalance &lt;chat_id&gt; &lt;số_tiền&gt;</code> - Cộng balance\n"
        f"<code>/subbalance &lt;chat_id&gt; &lt;số_tiền&gt;</code> - Trừ balance\n"
        f"<code>/kill &lt;chat_id&gt;</code> - Xoá toàn bộ data user\n"
        f"<code>/predcache</code> - Xem cache dự đoán hiện tại"
    )


@bot.message_handler(commands=['users'])
@admin_only
def admin_users(message):
    if not user_data and not auto_data:
        bot.reply_to(message, "📭 <b>Chưa có user nào.</b>")
        return
    lines = []
    all_ids = set(list(user_data.keys()) + list(auto_data.keys()))
    for cid in all_ids:
        u = user_data.get(cid, {})
        a = auto_data.get(cid, {})
        status = "🟢" if a.get("running") else "⚪"
        bal = a.get("balance", u.get("balance", 0))
        lines.append(f"{status} <code>{cid}</code> — {bal:,.0f}đ")
    bot.reply_to(message, "👥 <b>DANH SÁCH USER</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines))


@bot.message_handler(commands=['userinfo'])
@admin_only
def admin_userinfo(message):
    try:
        cid = int(message.text.split()[1])
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/userinfo &lt;chat_id&gt;</code>")
        return
    u = user_data.get(cid, {})
    a = auto_data.get(cid, {})
    if not u and not a:
        bot.reply_to(message, f"❌ Không có data cho <code>{cid}</code>")
        return
    txt = f"👤 <b>USER INFO:</b> <code>{cid}</code>\n━━━━━━━━━━━━━━━━━━━━\n"
    if u:
        txt += f"📌 <b>Step:</b> {u.get('step', '?')}\n"
        txt += f"💵 <b>Balance (user_data):</b> {u.get('balance', 0):,.0f}đ\n"
        txt += f"🎯 <b>Target:</b> {u.get('target', 0):,.0f}đ\n"
        txt += f"🎲 <b>Bet amount:</b> {u.get('bet_amount', 0):,.0f}đ\n"
    if a:
        txt += f"\n🟢 <b>Running:</b> {a.get('running')}\n"
        txt += f"📡 <b>Connected:</b> {a.get('connected')}\n"
        txt += f"💵 <b>Balance (auto):</b> {a.get('balance', 0):,.0f}đ\n"
        txt += f"🎲 <b>Vốn gốc:</b> {a.get('base_bet', 0):,.0f}đ\n"
        txt += f"🎲 <b>Vốn hiện tại:</b> {a.get('bet_amount', 0):,.0f}đ\n"
        txt += f"📊 <b>Chuỗi thua:</b> {a.get('lose_streak', 0)}/{MAX_LOSE_STREAK}\n"
        txt += f"⏳ <b>Chờ hồi:</b> {a.get('waiting_recover', False)}\n"
        txt += f"📈 <b>Ván:</b> {a.get('total_bet_count', 0)} | ✅ {a.get('total_win_count', 0)} | ❌ {a.get('total_lose_count', 0)}\n"
        txt += f"🎯 <b>Last predict:</b> {a.get('last_predict', '—')}\n"
        txt += f"🆔 <b>Last sid:</b> {a.get('last_sid', 0)}\n"
    bot.reply_to(message, txt)


@bot.message_handler(commands=['stopall'])
@admin_only
def admin_stopall(message):
    count = 0
    for cid, d in auto_data.items():
        if d.get("running"):
            d["running"] = False
            count += 1
            try:
                bot.send_message(cid, "🛑 <b>ADMIN ĐÃ DỪNG TOÀN BỘ AUTO.</b>")
            except:
                pass
    bot.reply_to(message, f"✅ <b>Đã dừng {count} tiến trình auto.</b>")


@bot.message_handler(commands=['broadcast'])
@admin_only
def admin_broadcast(message):
    try:
        content = message.text.split(" ", 1)[1]
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/broadcast &lt;nội_dung&gt;</code>")
        return
    all_ids = set(list(user_data.keys()) + list(auto_data.keys()))
    ok, fail = 0, 0
    for cid in all_ids:
        try:
            bot.send_message(cid, f"📢 <b>THÔNG BÁO TỪ ADMIN</b>\n━━━━━━━━━━━━━━━━━━━━\n{content}")
            ok += 1
        except:
            fail += 1
    bot.reply_to(message, f"📢 <b>Broadcast xong:</b> ✅ {ok} | ❌ {fail}")


@bot.message_handler(commands=['setrole'])
@admin_only
def admin_setrole(message):
    try:
        cid = int(message.text.split()[1])
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/setrole &lt;chat_id&gt;</code>")
        return
    if cid not in ADMIN_IDS:
        ADMIN_IDS.append(cid)
    bot.reply_to(message, f"✅ Đã thêm <code>{cid}</code> vào danh sách admin.\n<b>Lưu ý:</b> cần sửa ADMIN_IDS trong code để giữ vĩnh viễn.")


@bot.message_handler(commands=['unsetrole'])
@admin_only
def admin_unsetrole(message):
    try:
        cid = int(message.text.split()[1])
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/unsetrole &lt;chat_id&gt;</code>")
        return
    if cid in ADMIN_IDS:
        ADMIN_IDS.remove(cid)
        bot.reply_to(message, f"✅ Đã xoá <code>{cid}</code> khỏi admin.")
    else:
        bot.reply_to(message, f"⚠️ <code>{cid}</code> không trong danh sách admin.")


@bot.message_handler(commands=['addbalance'])
@admin_only
def admin_addbalance(message):
    try:
        parts = message.text.split()
        cid = int(parts[1]); amount = int(parts[2])
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/addbalance &lt;chat_id&gt; &lt;số_tiền&gt;</code>")
        return
    if cid in auto_data:
        auto_data[cid]["balance"] += amount
        new_bal = auto_data[cid]["balance"]
    elif cid in user_data:
        user_data[cid]["balance"] = user_data[cid].get("balance", 0) + amount
        new_bal = user_data[cid]["balance"]
    else:
        bot.reply_to(message, f"❌ Không tìm thấy user <code>{cid}</code>")
        return
    bot.reply_to(message, f"✅ Đã cộng <b>{amount:,.0f}đ</b> cho <code>{cid}</code>\n💵 Balance mới: <b>{new_bal:,.0f}đ</b>")
    try:
        bot.send_message(cid, f"💰 <b>ADMIN ĐÃ CỘNG {amount:,.0f}đ VÀO VÍ AUTO.</b>\n💵 Số dư mới: {new_bal:,.0f}đ")
    except:
        pass


@bot.message_handler(commands=['subbalance'])
@admin_only
def admin_subbalance(message):
    try:
        parts = message.text.split()
        cid = int(parts[1]); amount = int(parts[2])
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/subbalance &lt;chat_id&gt; &lt;số_tiền&gt;</code>")
        return
    if cid in auto_data:
        auto_data[cid]["balance"] -= amount
        new_bal = auto_data[cid]["balance"]
    elif cid in user_data:
        user_data[cid]["balance"] = user_data[cid].get("balance", 0) - amount
        new_bal = user_data[cid]["balance"]
    else:
        bot.reply_to(message, f"❌ Không tìm thấy user <code>{cid}</code>")
        return
    bot.reply_to(message, f"✅ Đã trừ <b>{amount:,.0f}đ</b> của <code>{cid}</code>\n💵 Balance mới: <b>{new_bal:,.0f}đ</b>")


@bot.message_handler(commands=['kill'])
@admin_only
def admin_kill(message):
    try:
        cid = int(message.text.split()[1])
    except:
        bot.reply_to(message, "⚠️ Dùng: <code>/kill &lt;chat_id&gt;</code>")
        return
    if cid in auto_data:
        auto_data[cid]["running"] = False
        del auto_data[cid]
    if cid in user_data:
        del user_data[cid]
    if cid in admin_broadcast_state:
        del admin_broadcast_state[cid]
    bot.reply_to(message, f"🗑 Đã xoá toàn bộ data của <code>{cid}</code>")


@bot.message_handler(commands=['predcache'])
@admin_only
def admin_predcache(message):
    c = _pred_cache
    bot.reply_to(message,
        f"📡 <b>PRED CACHE</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🔮 <b>Prediction:</b> {c.get('prediction') or '—'}\n"
        f"💯 <b>Confidence:</b> {c.get('confidence', 0)}%\n"
        f"📊 <b>Cầu:</b> {c.get('cau') or '—'}\n"
        f"🆔 <b>Phiên:</b> #{c.get('phien') or '—'}\n"
        f"🏁 <b>KQ phiên trước:</b> {c.get('ket_qua') or '—'}\n"
        f"🕒 <b>Age:</b> {round(time.time() - c.get('ts', 0), 1)}s")


print(f"[{time.strftime('%H:%M:%S')}] 🤖 ENGINE AI CORE v7.0 (Flask + Martingale V3) đã bật.")
print(f"[{time.strftime('%H:%M:%S')}] 📈 Chế độ: x2 khi thua | dừng tay {MAX_LOSE_STREAK} | reset khi thắng")
print(f"[{time.strftime('%H:%M:%S')}] 👑 ADMIN_IDS: {ADMIN_IDS}")
print(f"[{time.strftime('%H:%M:%S')}] 🌐 Flask health server đã chạy trên PORT {os.environ.get('PORT', 10000)}")
print(f"[{time.strftime('%H:%M:%S')}] 📱 Đang lắng nghe Telegram...")
websocket.enableTrace(False)
bot.infinity_polling()
