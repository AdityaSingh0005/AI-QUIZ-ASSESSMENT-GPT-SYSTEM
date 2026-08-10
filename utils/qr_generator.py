import qrcode
import os


def generate_qr(quiz_id):

    url = f"http://127.0.0.1:5000/start_quiz/{quiz_id}"

    print("QR URL:", url)

    img = qrcode.make(url)

    folder = "static/qr"
    os.makedirs(folder, exist_ok=True)

    file_name = f"quiz_{quiz_id}.png"

    path = os.path.join(folder, file_name)

    img.save(path)

    print("QR SAVED:", path)

    return path