import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch

import app
import account_store
import task_management
import task_store
from main import process_message_with_metadata


def model_response(action):
    return json.dumps(action)


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(
            os.environ,
            {"ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "tasks.sqlite3")},
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    def test_persistence_and_validation(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        task = task_store.create_task(
            {
                "title": "Send the logo request",
                "description": "Ask Alex for the approved asset.",
                "project": "Alex",
                "due_date": tomorrow,
                "priority": "high",
            }
        )
        self.assertEqual(task_store.get_task(task["id"]), task)
        self.assertEqual(task_store.list_tasks(project="alex")[0]["title"], "Send the logo request")
        self.assertEqual(task_store.list_tasks(view="today"), [])

        with self.assertRaisesRegex(ValueError, "valid calendar date"):
            task_store.create_task({"title": "Invalid date", "due_date": "2026-02-30"})
        with self.assertRaisesRegex(ValueError, "Priority"):
            task_store.create_task({"title": "Invalid priority", "priority": "urgent"})

    def test_updates_status_and_delete(self):
        task = task_store.create_task({"title": "Fix mobile layout"})
        updated = task_store.update_task(task["id"], {"due_date": "2026-10-27", "project": "Alex"})
        self.assertEqual(updated["due_date"], "2026-10-27")
        self.assertEqual(updated["title"], "Fix mobile layout")
        self.assertEqual(task_store.set_task_status(task["id"], "completed")["status"], "completed")
        self.assertEqual(len(task_store.list_tasks(view="completed")), 1)
        self.assertEqual(task_store.set_task_status(task["id"], "open")["status"], "open")
        self.assertTrue(task_store.delete_task(task["id"]))
        self.assertIsNone(task_store.get_task(task["id"]))
        self.assertFalse(task_store.delete_task(task["id"]))

    def test_subtask_is_stored_with_parent(self):
        parent = task_store.create_task({"title": "Prepare landing page"})
        child = task_store.create_tasks(
            [{"title": "Check mobile layout"}],
            parent_task_id=parent["id"],
        )[0]
        self.assertEqual(child["parent_task_id"], parent["id"])

    def test_invalid_priority_type_returns_validation_error(self):
        with self.assertRaisesRegex(ValueError, "Priority"):
            task_store.create_task({"title": "Prepare presentation", "priority": []})

    def test_duplicate_proposal_persists_and_new_tasks_in_a_batch_are_checked(self):
        first = task_store.create_task({"title": "Send the client proposal"})
        staged = task_store.stage_task_batch(
            [
                {"title": "Prepare Friday workshop materials"},
                {"title": "Send client proposal"},
            ],
            "persistent-conversation",
        )
        self.assertEqual(len(staged["created"]), 1)
        self.assertEqual(staged["created"][0]["title"], "Prepare Friday workshop materials")
        self.assertEqual(len(staged["proposals"]), 1)
        self.assertEqual(staged["proposals"][0]["candidates"][0]["id"], first["id"])
        persisted = task_store.list_task_proposals("persistent-conversation")
        self.assertEqual(persisted[0]["id"], staged["proposals"][0]["id"])
        self.assertIsNone(persisted[0]["task"].get("priority"))


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(
            os.environ,
            {"ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "tasks.sqlite3")},
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        app.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
        app.limiter.reset()
        app.limiter.enabled = False
        self.client = app.app.test_client()
        account_store.initialize()
        self.user = account_store.create_user("tasks@example.com", "strong-password-123")
        account_store.verify_email(self.user["id"])
        self.login(self.client, self.user)

    @staticmethod
    def login(client, user):
        response = client.post(
            "/auth/login",
            data={"email": user["email"], "password": "strong-password-123"},
        )
        if response.status_code != 302:
            raise AssertionError(f"Test account sign in failed: {response.status_code}")

    def test_task_crud_endpoints_validate_and_persist(self):
        response = self.client.post(
            "/api/tasks",
            json={"title": "Finish landing page", "project": "Alex"},
        )
        self.assertEqual(response.status_code, 201)
        task = response.get_json()["task"]

        response = self.client.get("/api/tasks?view=open&project=alex")
        self.assertEqual([item["id"] for item in response.get_json()["tasks"]], [task["id"]])
        self.assertEqual(
            self.client.patch(f"/api/tasks/{task['id']}", json={"due_date": "bad-date"}).status_code,
            400,
        )
        response = self.client.patch(
            f"/api/tasks/{task['id']}/status",
            json={"status": "completed"},
        )
        self.assertEqual(response.get_json()["task"]["status"], "completed")
        self.assertEqual(self.client.get("/api/tasks?view=completed").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}").status_code, 404)

    def test_duplicate_choice_endpoint_resolves_only_once(self):
        task = task_store.create_task({"title": "Send the proposal"}, user_id=self.user["id"])
        staged = task_store.stage_task_batch(
            [{"title": "Send proposal"}],
            "conversation-test",
            user_id=self.user["id"],
        )
        proposal_id = staged["proposals"][0]["id"]
        response = self.client.post(
            "/api/tasks/confirm-duplicate",
            json={
                "proposal_id": proposal_id,
                "candidate_id": task["id"],
                "choice": "update_existing",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Updated", response.get_json()["reply"])
        self.assertEqual(
            self.client.post(
                "/api/tasks/confirm-duplicate",
                json={
                    "proposal_id": proposal_id,
                    "candidate_id": task["id"],
                    "choice": "create_separate",
                },
            ).status_code,
            404,
        )

    def test_concurrent_duplicate_choices_resolve_only_once(self):
        task_store.create_task({"title": "Send the proposal"}, user_id=self.user["id"])
        staged = task_store.stage_task_batch(
            [{"title": "Send proposal"}],
            "concurrent-conversation",
            user_id=self.user["id"],
        )
        proposal_id = staged["proposals"][0]["id"]
        request_data = {
            "proposal_id": proposal_id,
            "choice": "create_separate",
        }

        def resolve_proposal():
            with app.app.test_client() as client:
                self.login(client, self.user)
                return client.post("/api/tasks/confirm-duplicate", json=request_data).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: resolve_proposal(), range(2)))

        self.assertCountEqual(statuses, [200, 404])
        self.assertEqual(len(task_store.list_tasks(view="all", user_id=self.user["id"])), 2)


class ConversationalTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_patch = patch.dict(
            os.environ,
            {"ENNOIC_TASK_DB": os.path.join(self.temp_dir.name, "tasks.sqlite3")},
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

    @patch("task_management.ask_cloud_model")
    def test_brain_dump_creates_multiple_explicit_tasks_and_date(self, ask_model):
        ask_model.return_value = model_response(
            {
                "action": "create",
                "tasks": [
                    {
                        "title": "Finish Alex's landing page",
                        "description": None,
                        "project": "Alex",
                        "due_date": "2026-10-23",
                        "priority": "medium",
                    },
                    {
                        "title": "Ask Alex for the logo",
                        "description": None,
                        "project": "Alex",
                        "due_date": None,
                        "priority": "medium",
                    },
                    {
                        "title": "Fix the mobile layout",
                        "description": None,
                        "project": "Alex",
                        "due_date": None,
                        "priority": "medium",
                    },
                ],
            }
        )
        result = process_message_with_metadata(
            "I need to finish Alex's landing page by Friday, ask him for the logo, "
            "and fix the mobile layout."
        )
        tasks = task_store.list_tasks(view="all")
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["due_date"], "2026-10-23")
        self.assertIsNone(next(task for task in tasks if "logo" in task["title"].lower())["due_date"])
        self.assertIn("Created 3 tasks", result["reply"])

    @patch("task_management.ask_cloud_model")
    def test_similar_new_task_prompts_for_choice_without_creating_duplicate(self, ask_model):
        original = task_store.create_task(
            {
                "title": "Ask Alex for the logo",
                "description": "Request the approved SVG.",
                "project": "Alex",
                "due_date": "2026-10-29",
                "priority": "high",
            }
        )
        ask_model.return_value = model_response(
            {
                "action": "create",
                "tasks": [
                    {
                        "title": "Ask Alex about the logo",
                        "description": None,
                        "project": None,
                        "due_date": None,
                        "priority": None,
                    }
                ],
            }
        )
        result = task_management.maybe_handle_task_message(
            "I need to ask Alex about the logo.",
            [],
            "conversation-1",
        )
        tasks = task_store.list_tasks(view="all")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], original["id"])
        self.assertIn("may already exist", result["reply"])
        self.assertEqual(len(result["task_confirmations"]), 1)
        self.assertEqual(result["task_confirmations"][0]["candidates"][0]["id"], original["id"])

    @patch("task_management.ask_cloud_model")
    def test_update_existing_choice_keeps_unmentioned_task_details(self, ask_model):
        original = task_store.create_task(
            {
                "title": "Ask Alex for the logo",
                "description": "Request the approved SVG.",
                "project": "Alex",
                "due_date": "2026-10-29",
                "priority": "high",
            }
        )
        ask_model.return_value = model_response(
            {
                "action": "create",
                "tasks": [
                    {
                        "title": "Ask Alex about the logo",
                        "description": "Ask for the final SVG.",
                        "project": None,
                        "due_date": None,
                        "priority": None,
                    }
                ],
            }
        )
        result = task_management.maybe_handle_task_message(
            "I need to ask Alex about the logo.",
            [],
            "conversation-2",
        )
        proposal = result["task_confirmations"][0]
        ask_model.assert_called_once()
        resolved = task_management.resolve_task_confirmation(
            proposal["id"],
            "update_existing",
            original["id"],
        )
        updated = task_store.get_task(original["id"])
        self.assertIn("Existing title and unspecified details were kept", resolved["reply"])
        self.assertEqual(updated["title"], "Ask Alex for the logo")
        self.assertEqual(updated["description"], "Ask for the final SVG.")
        self.assertEqual(updated["project"], "Alex")
        self.assertEqual(updated["due_date"], "2026-10-29")
        self.assertEqual(updated["priority"], "high")
        self.assertEqual(len(task_store.list_tasks(view="all")), 1)

    @patch("task_management.ask_cloud_model")
    def test_separate_and_cancel_choices_do_not_overwrite_original(self, ask_model):
        original = task_store.create_task(
            {"title": "Send the proposal", "project": "Northwind", "priority": "high"}
        )
        ask_model.return_value = model_response(
            {
                "action": "create",
                "tasks": [
                    {
                        "title": "Send proposal",
                        "description": None,
                        "project": None,
                        "due_date": None,
                        "priority": None,
                    }
                ],
            }
        )
        result = task_management.maybe_handle_task_message(
            "I need to send proposal.",
            [],
            "conversation-3",
        )
        separate = result["task_confirmations"][0]
        task_management.resolve_task_confirmation(separate["id"], "create_separate")
        tasks = task_store.list_tasks(view="all")
        self.assertEqual(len(tasks), 2)
        self.assertEqual(task_store.get_task(original["id"])["priority"], "high")
        self.assertEqual(task_store.get_task(original["id"])["project"], "Northwind")

        result = task_management.maybe_handle_task_message(
            "I need to send proposal.",
            [],
            "conversation-3",
        )
        cancel = result["task_confirmations"][0]
        task_management.resolve_task_confirmation(cancel["id"], "cancel")
        self.assertEqual(len(task_store.list_tasks(view="all")), 2)

    @patch("task_management.ask_cloud_model")
    def test_distinct_task_is_created_without_duplicate_confirmation(self, ask_model):
        task_store.create_task({"title": "Call Alex about the logo"})
        ask_model.return_value = model_response(
            {
                "action": "create",
                "tasks": [
                    {
                        "title": "Book train tickets for Friday",
                        "description": None,
                        "project": None,
                        "due_date": None,
                        "priority": None,
                    }
                ],
            }
        )
        result = task_management.maybe_handle_task_message(
            "I need to book train tickets for Friday.",
            [],
            "conversation-4",
        )
        self.assertEqual(result["task_confirmations"], [])
        self.assertEqual(len(task_store.list_tasks(view="all")), 2)

    @patch("task_management.ask_cloud_model")
    def test_clear_existing_task_edit_still_updates_without_duplicate_prompt(self, ask_model):
        task = task_store.create_task(
            {"title": "Send the proposal", "due_date": "2026-10-23", "priority": "high"}
        )
        ask_model.return_value = model_response(
            {
                "action": "update",
                "target": "Send the proposal",
                "updates": {"due_date": "2026-10-27"},
            }
        )
        result = task_management.maybe_handle_task_message(
            "Move the Send the proposal deadline to Tuesday.",
            [],
            "conversation-5",
        )
        self.assertIn("Updated", result["reply"])
        self.assertEqual(task_store.get_task(task["id"])["due_date"], "2026-10-27")
        self.assertEqual(result.get("task_confirmations", []), [])

    @patch("task_management.ask_cloud_model")
    def test_typed_confirmation_resolves_single_pending_proposal(self, ask_model):
        original = task_store.create_task({"title": "Review the launch plan"})
        ask_model.return_value = model_response(
            {
                "action": "create",
                "tasks": [
                    {
                        "title": "Review launch plan",
                        "description": None,
                        "project": None,
                        "due_date": None,
                        "priority": None,
                    }
                ],
            }
        )
        created = task_management.maybe_handle_task_message(
            "I need to review launch plan.",
            [],
            "conversation-6",
        )
        proposal_id = created["task_confirmations"][0]["id"]
        resolved = task_management.maybe_handle_task_message(
            "create a separate task",
            [],
            "conversation-6",
        )
        self.assertEqual(resolved["resolved_proposal_id"], proposal_id)
        self.assertEqual(len(task_store.list_tasks(view="all")), 2)
        self.assertEqual(task_store.get_task(original["id"])["title"], "Review the launch plan")

    @patch("task_management.ask_cloud_model")
    def test_project_retrieval_status_changes_and_reschedule(self, ask_model):
        task = task_store.create_task({"title": "Ask Alex for the logo", "project": "Alex"})
        task_store.create_task({"title": "Buy groceries", "project": "Home"})
        ask_model.return_value = model_response(
            {"action": "list", "project": "Alex", "status": "open", "view": "open"}
        )
        result = task_management.maybe_handle_task_message(
            "What should I finish for Alex?", []
        )
        self.assertIn("Ask Alex for the logo", result["reply"])
        self.assertNotIn("Buy groceries", result["reply"])

        ask_model.return_value = model_response(
            {"action": "update", "target": "Ask Alex for the logo", "updates": {"due_date": "2026-10-27"}}
        )
        result = task_management.maybe_handle_task_message(
            "Move the logo deadline to Tuesday.", []
        )
        self.assertIn("Updated", result["reply"])
        self.assertEqual(task_store.get_task(task["id"])["due_date"], "2026-10-27")

        ask_model.return_value = model_response(
            {"action": "complete", "target": "Ask Alex for the logo"}
        )
        task_management.maybe_handle_task_message("Mark the logo task complete.", [])
        self.assertEqual(task_store.get_task(task["id"])["status"], "completed")
        ask_model.return_value = model_response(
            {"action": "reopen", "target": "Ask Alex for the logo"}
        )
        task_management.maybe_handle_task_message("Reopen the logo task.", [])
        self.assertEqual(task_store.get_task(task["id"])["status"], "open")

    @patch("task_management.ask_cloud_model")
    def test_ambiguous_task_reference_does_not_modify_either_task(self, ask_model):
        first = task_store.create_task({"title": "Ask Alex for the logo"})
        second = task_store.create_task({"title": "Ask Sam for the logo"})
        ask_model.return_value = model_response({"action": "complete", "target": "logo"})
        result = task_management.maybe_handle_task_message("Complete the logo task.", [])
        self.assertIn("more than one", result["reply"])
        self.assertEqual(task_store.get_task(first["id"])["status"], "open")
        self.assertEqual(task_store.get_task(second["id"])["status"], "open")

    @patch("task_management.ask_cloud_model")
    def test_malformed_model_output_fails_without_creating_tasks(self, ask_model):
        ask_model.return_value = "not JSON"
        with self.assertRaisesRegex(ValueError, "could not be interpreted"):
            task_management.maybe_handle_task_message("Add a task to finish the report.", [])
        self.assertEqual(task_store.list_tasks(view="all"), [])

    @patch("task_management.ask_cloud_model")
    def test_general_chat_is_not_treated_as_a_task_operation(self, ask_model):
        self.assertIsNone(task_management.maybe_handle_task_message("What is the weather?", []))
        ask_model.assert_not_called()

    @patch("task_management.ask_cloud_model")
    def test_next_task_uses_nearest_deadline_and_priority(self, ask_model):
        task_store.create_task({"title": "High priority, no due date", "priority": "high"})
        urgent = task_store.create_task(
            {"title": "Send the proposal", "due_date": "2026-10-23", "priority": "high"}
        )
        ask_model.return_value = model_response({"action": "next"})
        result = task_management.maybe_handle_task_message("What should I work on next?", [])
        self.assertIn(urgent["title"], result["reply"])

    @patch("task_management.ask_cloud_model")
    def test_split_creates_children_and_delete_removes_target(self, ask_model):
        parent = task_store.create_task({"title": "Prepare the presentation"})
        ask_model.return_value = model_response(
            {
                "action": "split",
                "target": parent["title"],
                "tasks": [{"title": "Draft the outline"}, {"title": "Review the slides"}],
            }
        )
        result = task_management.maybe_handle_task_message(
            "Break the presentation task into subtasks.", []
        )
        children = task_store.list_tasks(view="all")
        self.assertIn("Added 2 subtasks", result["reply"])
        self.assertEqual(
            {task["title"] for task in children if task["parent_task_id"] == parent["id"]},
            {"Draft the outline", "Review the slides"},
        )

        ask_model.return_value = model_response({"action": "delete", "target": parent["title"]})
        result = task_management.maybe_handle_task_message("Delete the presentation task.", [])
        self.assertIn("Deleted", result["reply"])
        self.assertEqual(task_store.list_tasks(view="all"), [])


if __name__ == "__main__":
    unittest.main()
