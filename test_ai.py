from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import utils.ai_generator as ai_generator


# Check Ollama configuration
if not ai_generator.ollama_client:
    raise RuntimeError("Ollama is not configured in .env")


# Save original function
original_request = ai_generator._request_with_fallback


# Simulate failure of normal Groq + Ollama attempts
def simulate_primary_failure(*args, **kwargs):
    raise RuntimeError(
        "TEST: Simulated failure of normal provider attempts"
    )


ai_generator._request_with_fallback = simulate_primary_failure


try:
    questions, evaluations = ai_generator.generate_questions(
        "Data Structures",
        1,  # Easy
        0,  # Medium
        0   # Hard
    )

    print("\nTotal Questions:", len(questions))
    print("Evaluations:", evaluations)

    for i, q in enumerate(questions, 1):
        print(f"\n{i}. [{q.get('difficulty')}] {q.get('question')}")
        print("Correct:", q.get("correct_answer"))
        print("Explanation:", q.get("explanation"))
        print("\nQUESTION DATA:", questions[0])
        print("\nAVAILABLE FIELDS:", questions[0].keys())

finally:
    # Restore original function
    ai_generator._request_with_fallback = original_request