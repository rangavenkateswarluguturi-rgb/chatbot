import tempfile
import unittest
from unittest.mock import patch

from backend import create_app


class TechTutorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE_PATH": f"{self.temp_dir.name}/test.db",
            "DEFAULT_MODEL": "llama3.2:latest",
            "MAX_MESSAGE_LENGTH": 8000,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_homepage_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TechTutor", response.data)

    def test_models_lists_installed_local_models(self):
        with patch("backend.routes.ollama_request", return_value={
            "models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5:latest"}]
        }):
            response = self.client.get("/api/models")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["model"], "llama3.2:latest")
        self.assertEqual(len(response.json["models"]), 2)

    def test_chat_saves_real_model_answer_and_history(self):
        with patch("backend.routes.ollama_request", return_value={
            "message": {"content": "A variable stores a value."}
        }) as ollama:
            response = self.client.post("/api/chat", json={
                "message": "What is a variable?", "subject": "Programming",
                "model": "llama3.2:latest", "level": "Beginner",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["answer"], "A variable stores a value.")
        self.assertEqual(ollama.call_args.args[0], "/api/chat")
        history = self.client.get(f"/api/chats/{response.json['chat_id']}")
        self.assertEqual(len(history.json["messages"]), 2)
        self.assertEqual(history.json["messages"][1]["content"], "A variable stores a value.")

    def test_chat_rejects_invalid_input_before_calling_model(self):
        with patch("backend.routes.ollama_request") as ollama:
            response = self.client.post("/api/chat", json={
                "message": "Hello", "subject": "Not a subject"
            })
        self.assertEqual(response.status_code, 400)
        ollama.assert_not_called()

    def test_chat_rejects_unhashable_subject_and_invalid_level(self):
        for data in (
            {"message": "Hello", "subject": []},
            {"message": "Hello", "level": ["Beginner"]},
        ):
            with self.subTest(data=data):
                response = self.client.post("/api/chat", json=data)
                self.assertEqual(response.status_code, 400)

    def test_oversized_messages_receive_json_error(self):
        response = self.client.post("/api/chat", json={
            "message": "x" * 8001
        })
        self.assertEqual(response.status_code, 413)
        self.assertIn("8,000 characters", response.json["error"])

    def test_model_failure_is_reported_and_not_saved_as_an_answer(self):
        with patch("backend.routes.ollama_request", side_effect=RuntimeError(
            "Cannot connect to Ollama. Open the Ollama app."
        )):
            response = self.client.post("/api/chat", json={"message": "Hello"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Cannot connect to Ollama", response.json["error"])
        chat_id = response.json["chat_id"]
        history = self.client.get(f"/api/chats/{chat_id}")
        self.assertEqual([item["role"] for item in history.json["messages"]], ["user"])

    def test_retry_uses_same_chat_without_replaying_unanswered_prompt(self):
        with patch("backend.routes.ollama_request", side_effect=[
            RuntimeError("Cannot connect to Ollama."),
            {"message": {"content": "A variable stores a value."}},
        ]) as ollama:
            first = self.client.post("/api/chat", json={"message": "What is a variable?"})
            second = self.client.post("/api/chat", json={
                "message": "Try that again.", "chat_id": first.json["chat_id"]
            })
        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 200)
        sent_messages = ollama.call_args_list[1].kwargs["json"]["messages"]
        self.assertEqual([message["role"] for message in sent_messages], ["system", "user"])
        self.assertEqual(sent_messages[-1]["content"], "Try that again.")

    def test_preferences_validate_installed_models(self):
        with patch("backend.routes.ollama_request", return_value={"models": []}):
            response = self.client.put("/api/preferences", json={"model": "missing:model"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not installed", response.json["error"])

    def test_clear_history_deletes_chats(self):
        self.client.post("/api/chat", json={"message": "Hello"})
        response = self.client.delete("/api/chats")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/chats").json, [])


if __name__ == "__main__":
    unittest.main()
