import qrcode
import os


def generate_qr(quiz_id):

    # ============================================================
    # PRODUCTION / LOCAL URL
    # ============================================================

    base_url = os.getenv(
        "APP_URL",
        "https://ai-quiz-assessment-gpt-system.onrender.com"
    ).rstrip("/")

    # ============================================================
    # QR TARGET URL
    # ============================================================

    # Guest quiz entry page
    url = f"{base_url}/guest_start_quiz/{quiz_id}"

    print("========================================")
    print("QR URL:", url)
    print("========================================")

    # ============================================================
    # QR FOLDER
    # ============================================================

    folder = os.path.join(
        "static",
        "qr"
    )

    os.makedirs(
        folder,
        exist_ok=True
    )

    # ============================================================
    # FILE NAME
    # ============================================================

    file_name = f"quiz_{quiz_id}.png"

    path = os.path.join(
        folder,
        file_name
    )

    # ============================================================
    # GENERATE QR
    # ============================================================

    img = qrcode.make(url)

    img.save(path)

    print("QR SAVED:", path)
    print("QR EXISTS:", os.path.exists(path))

    # ============================================================
    # RETURN WEB PATH
    # ============================================================

    return f"static/qr/{file_name}"