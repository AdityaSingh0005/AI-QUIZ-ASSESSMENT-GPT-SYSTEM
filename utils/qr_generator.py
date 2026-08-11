import qrcode
import os


def generate_qr(quiz_id):

    # Production URL
    base_url = os.getenv(
        "APP_URL",
        "https://ai-quiz-assessment-gpt-system.onrender.com"
    ).rstrip("/")

    # QR will open this public URL
    url = f"{base_url}/start_quiz/{quiz_id}"

    print("QR URL:", url)

    img = qrcode.make(url)

    folder = "static/qr"
    os.makedirs(folder, exist_ok=True)

    file_name = f"quiz_{quiz_id}.png"

    path = os.path.join(folder, file_name)

    img.save(path)

    print("QR SAVED:", path)

    return path