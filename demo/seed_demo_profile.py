"""
Demo seed script — tạo profile AI giả cho demo.

Persona: Học sinh đi xe máy
- Thứ 2–6: đi học lúc 7:30 sáng
- Chủ nhật: thể thao lúc 17:00

Chạy:
    python demo/seed_demo_profile.py <instanceId>

instanceId lấy từ app: Settings → copy từ debug log "[AppInstance] id=..."
"""
import sys
import os
import math
from datetime import datetime, timezone, timedelta
import random

# Thêm root vào path để import app config
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import firebase_admin
from firebase_admin import credentials, firestore

# ---------------------------------------------------------------------------
# Firebase init
# ---------------------------------------------------------------------------

def _init_firebase():
    secret_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "secrets")
    key_candidates = [f for f in os.listdir(secret_dir) if f.endswith(".json")] if os.path.isdir(secret_dir) else []
    if not firebase_admin._apps:
        if key_candidates:
            cred = credentials.Certificate(os.path.join(secret_dir, key_candidates[0]))
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()  # ADC


# ---------------------------------------------------------------------------
# Weights đã "học" cho persona xe máy + học sinh
# Được tính từ gradient descent giả lập 50 lần tương tác:
#   - Mở thông báo mưa/gió lúc sáng đi học → tăng w_rain, w_wind, w_schedule
#   - Bỏ qua thông báo ban đêm            → tăng penalty cho dismiss
# ---------------------------------------------------------------------------

DEMO_WEIGHTS = [
    4.92,   # f0:  rain_prob      — mưa rất quan trọng với xe máy
    4.15,   # f1:  wind_norm      — gió ảnh hưởng nhiều khi đi xe
    1.80,   # f2:  has_thunder    — dông → nguy hiểm cao
    0.35,   # f3:  feels_norm     — nhiệt độ ít quan trọng hơn
    3.20,   # f4:  is_in_schedule — chỉ cần biết khi đang đi học/chơi
   -0.08,   # f5:  day_of_week
    0.40,   # f6:  is_weekend
    2.80,   # f7:  open_rate_7d   — hay mở → tích cực
   -4.60,   # f8:  dismiss_rate_7d — hay bỏ qua → trừ điểm nặng
   -1.20,   # f9:  lead_time
    1.95,   # f10: transport_risk  — xe máy = 1.0 → hệ số cao
    4.80,   # f11: is_outdoor_now  — đang ở ngoài mới thực sự cần
]
DEMO_BIAS = -6.10

DEMO_SCHEDULE = [
    {"days": [1, 2, 3, 4, 5], "time": "07:30", "activity": "commute"},
    {"days": [7],              "time": "17:00", "activity": "sport"},
]


# ---------------------------------------------------------------------------
# Sinh fake alert_logs để recentOpenRate7d có số liệu thực
# ---------------------------------------------------------------------------

def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _gen_fake_interactions(db, instance_id: str, count: int = 60):
    """
    Tạo fake alert_logs trong 7 ngày qua.
    Người đi học → mở mưa/gió buổi sáng, bỏ qua buổi tối.
    """
    now = datetime.now(timezone.utc)
    batch = db.batch()
    opened = 0
    dismissed = 0

    alert_types = ["heavy_rain", "strong_wind", "thunderstorm", "cold_snap", "heatwave"]
    locations = ["home", "school"]

    for i in range(count):
        days_ago = random.randint(0, 6)
        sent_at = now - timedelta(days=days_ago)

        # Sáng đi học (7–9h) T2-T6 → hay mở
        is_morning_weekday = (sent_at.weekday() < 5) and (7 <= sent_at.hour <= 9)
        # Chiều CN thể thao (16–18h) → hay mở
        is_sport_time = (sent_at.weekday() == 6) and (16 <= sent_at.hour <= 18)
        # Còn lại → hay bỏ qua

        if is_morning_weekday or is_sport_time:
            outcome = "opened" if random.random() < 0.82 else "dismissed"
        else:
            outcome = "dismissed" if random.random() < 0.75 else "opened"

        if outcome == "opened":
            opened += 1
        else:
            dismissed += 1

        alert_type = random.choice(alert_types)
        key = f"demo__{instance_id}__{alert_type}__fake_{i}"
        doc_ref = db.collection("alert_logs").document(key)
        batch.set(doc_ref, {
            "sentAt": sent_at,
            "alertType": alert_type,
            "locationId": f"loc_demo_{random.choice(locations)}",
            "deviceId": instance_id,
            "outcome": outcome,
            "expireAt": now + timedelta(days=1),
            "_demo": True,
        })

    batch.commit()
    total = opened + dismissed
    print(f"  ✓ Tạo {total} fake interactions: opened={opened} ({opened*100//total}%), dismissed={dismissed} ({dismissed*100//total}%)")
    return opened / total, dismissed / total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed(instance_id: str):
    _init_firebase()
    db = firestore.client()

    print(f"\n🎯 Seeding demo profile cho instanceId: {instance_id}")
    print("   Persona: Học sinh đi xe máy, T2-T6 đi học 7:30, CN thể thao 17:00\n")

    # 1. Tạo fake interactions
    print("1. Tạo lịch sử tương tác giả...")
    open_rate, dismiss_rate = _gen_fake_interactions(db, instance_id, count=60)

    # 2. Ghi profile lên Firestore
    print("2. Ghi profile AI...")
    device_ref = db.collection("devices").document(instance_id)

    # Đảm bảo device doc tồn tại
    device_doc = device_ref.get()
    if not device_doc.exists:
        device_ref.set({
            "platform": "ios",
            "timezone": "Asia/Ho_Chi_Minh",
            "isActive": True,
            "updatedAt": datetime.now(timezone.utc),
        }, merge=True)
        print("   ✓ Tạo device doc (chưa có)")

    device_ref.set({
        "aiPersonalization": {
            "enabled": True,
            "occupation": "student",
            "transport": "motorbike",
            "schedule": DEMO_SCHEDULE,
            "weights": DEMO_WEIGHTS,
            "bias": DEMO_BIAS,
            "updateCount": 47,          # giả lập đã học 47 lần
            "learningRate": 0.01,
            "recentOpenRate7d": round(open_rate, 3),
            "recentDismissRate7d": round(dismiss_rate, 3),
            "updatedAt": datetime.now(timezone.utc),
        }
    }, merge=True)

    print(f"   ✓ openRate7d={open_rate:.0%}, dismissRate7d={dismiss_rate:.0%}")
    print(f"   ✓ weights: {len(DEMO_WEIGHTS)} features, bias={DEMO_BIAS}")
    print(f"   ✓ schedule: {DEMO_SCHEDULE}")

    # 3. Xác nhận bằng cách tính thử probability
    print("\n3. Kiểm tra probability mẫu:")
    _show_probability_samples()

    print("\n✅ Done! Giờ vào app bật AI và nhấn 'Kiểm tra thời tiết ngay'")
    print(f"   App sẽ dùng profile này để cá nhân hóa nội dung thông báo.\n")


def _show_probability_samples():
    """Tính thử probability cho 2 kịch bản để verify."""
    scenarios = [
        {
            "label": "Sáng đi học (T3 7:30) — mưa lớn",
            # f0 rain=0.8, f1 wind=0.35, f2 thunder=0, f3 feels=-0.1,
            # f4 in_schedule=1, f5 dow=2/7, f6 weekend=0,
            # f7 open=0.78, f8 dismiss=0.22, f9 lead=0.25, f10 transport=1.0, f11 outdoor=1
            "features": [0.8, 0.35, 0, -0.1, 1, 2/7, 0, 0.78, 0.22, 0.25, 1.0, 1],
        },
        {
            "label": "Tối thứ 4 (22:00) — mưa lớn",
            # Ngoài lịch, không outdoor
            "features": [0.8, 0.35, 0, -0.1, 0, 3/7, 0, 0.78, 0.22, 0.25, 1.0, 0],
        },
        {
            "label": "CN 17:00 thể thao — dông",
            # Weekend, in_schedule=1 (sport), thunder=1, outdoor=1
            "features": [0.5, 0.4, 1, 0, 1, 7/7, 1, 0.78, 0.22, 0.25, 1.0, 1],
        },
    ]
    for s in scenarios:
        z = DEMO_BIAS + sum(w * f for w, f in zip(DEMO_WEIGHTS, s["features"]))
        prob = _sigmoid(z)
        bar = "█" * int(prob * 20) + "░" * (20 - int(prob * 20))
        print(f"   {bar} {prob:.0%}  {s['label']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python demo/seed_demo_profile.py <instanceId>")
        sys.exit(1)
    seed(sys.argv[1])
