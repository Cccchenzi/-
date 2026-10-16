"""OAuth/stream security regressions: real RSA/DPAPI, mocked remote services.

These tests use newly generated RSA keys and synthetic credentials. They never
read an actual user's credentials or invoke a real ChatGPT account/model.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import create_app, providers
from backend import chatgpt_auth as auth


class SignedIdentityMixin:
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private_key.public_key()))
        cls.jwk.update(kid="synthetic-test-key", alg="RS256", use="sig")

    def signed(self, client_id="synthetic-client", nonce="test-nonce", subject="subject-A", **changes):
        claims = {"iss": auth.ISSUER, "aud": client_id, "exp": int(time.time()) + 3600,
                  "iat": int(time.time()), "sub": subject, "nonce": nonce, "email": "alice@example.invalid"}
        claims.update(changes)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.jwk["kid"]})


class IdentitySignatureTests(SignedIdentityMixin, unittest.TestCase):
    def setUp(self):
        self.http = patch("backend.chatgpt_auth._http", return_value={"keys": [self.jwk]}).start()
        self.addCleanup(patch.stopall)

    def test_real_rsa_signature_accepts_matching_identity_and_binding(self):
        token = self.signed()
        result = auth._validate_identity(token, "synthetic-client", "test-nonce", "subject-A")
        self.assertEqual(result["sub"], "subject-A")
        self.http.assert_called_once_with(auth.ISSUER + "/.well-known/jwks.json")

    def test_signed_wrong_nonce_issuer_audience_expiry_or_subject_is_rejected(self):
        fixtures = {
            "nonce": self.signed(nonce="different"),
            "issuer": self.signed(iss="https://attacker.invalid"),
            "audience": self.signed(aud="another-app"),
            "expired": self.signed(exp=int(time.time()) - 120),
            "different-subject": self.signed(subject="subject-B"),
            "missing-subject": self.signed(sub=None),
        }
        for name, token in fixtures.items():
            with self.subTest(case=name), self.assertRaises(auth.ChatGPTError):
                auth._validate_identity(token, "synthetic-client", "test-nonce", "subject-A")

    def test_forged_rsa_signature_or_unapproved_algorithm_is_rejected(self):
        claims = jwt.decode(self.signed(), options={"verify_signature": False})
        forged = jwt.encode(claims, self.other_key, algorithm="RS256", headers={"kid": self.jwk["kid"]})
        hs_token = jwt.encode(claims, "synthetic-signing-secret-32bytes!!", algorithm="HS256",
                              headers={"kid": self.jwk["kid"]})
        for token in [forged, hs_token, "not-a-jwt"]:
            with self.subTest(kind=token.split(".")[0]), self.assertRaises(auth.ChatGPTError):
                auth._validate_identity(token, "synthetic-client", "test-nonce")

    def test_token_fields_reject_invalid_expiry_and_token_types(self):
        valid = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                 "id_token": "synthetic-id", "token_type": "Bearer", "expires_in": 3600, "scope": auth.SCOPES}
        for field, value in [("expires_in", float("nan")), ("expires_in", float("inf")),
                             ("expires_in", True), ("expires_in", 0), ("expires_in", -1),
                             ("access_token", []), ("refresh_token", []), ("id_token", []),
                             ("token_type", []), ("scope", [])]:
            with self.subTest(field=field, value=value), self.assertRaises(auth.ChatGPTError):
                auth._token_fields(dict(valid, **{field: value}))


def stream(*events):
    return io.BytesIO(b"".join(("data: " + (event if isinstance(event, str) else json.dumps(event)) + "\n\n").encode()
                              for event in events))


class CompletedStreamTests(unittest.TestCase):
    body = {"insufficient": True, "claims": []}

    def test_completed_event_accepts_delta_or_completed_output(self):
        value = json.dumps(self.body)
        events = [{"type": "response.output_text.delta", "delta": value[:8]},
                  {"type": "response.output_text.delta", "delta": value[8:]},
                  {"type": "response.completed", "response": {"status": "completed"}}]
        self.assertEqual(auth.consume_response(stream(*events, "[DONE]")), self.body)
        completed = {"type": "response.completed", "response": {"status": "completed", "output": [
            {"content": [{"type": "output_text", "text": value}]}]}}
        self.assertEqual(auth.consume_response(stream(completed)), self.body)

    def test_delta_only_done_failed_and_incomplete_never_count_as_success(self):
        delta = {"type": "response.output_text.delta", "delta": json.dumps(self.body)}
        fixtures = [[delta], [delta, "[DONE]"], [delta, {"type": "response.failed"}],
                    [delta, {"type": "response.incomplete"}], [delta, {"type": "error"}],
                    [{"type": "response.completed", "response": {}}]]
        for events in fixtures:
            with self.subTest(events=events), self.assertRaises(auth.ChatGPTError):
                auth.consume_response(stream(*events))

    def test_malformed_utf8_json_or_nonobject_result_is_safely_rejected(self):
        streams = [io.BytesIO(b"data: \xff\n\n"), stream("PRIVATE_SERVER_BODY not json"),
                   stream({"type": "response.output_text.delta", "delta": "[]"}, {"type": "response.completed"})]
        for response in streams:
            with self.subTest(response=response), self.assertRaises(auth.ChatGPTError) as caught:
                auth.consume_response(response)
            self.assertNotIn("PRIVATE_SERVER_BODY", str(caught.exception))


class CredentialStorageTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "The production DPAPI path requires Windows.")
    def test_real_windows_dpapi_roundtrip_and_credential_file_is_not_plaintext(self):
        raw = b"synthetic-access-secret-never-a-real-token"
        encrypted = auth._protect(raw)
        self.assertNotEqual(encrypted, raw)
        self.assertNotIn(raw, encrypted)
        self.assertEqual(auth._protect(encrypted, decrypt=True), raw)
        with tempfile.TemporaryDirectory(prefix="zhiwei-dpapi-test-") as directory:
            store = auth.CredentialStore(directory)
            record = {"profiles": [{"id": "p1", "client_id": "synthetic-client", "access_token": raw.decode()}],
                      "active": "p1", "epoch": 0}
            store.write(1, record)
            encoded = (Path(directory) / "user-1.bin").read_bytes()
            self.assertTrue(encoded.startswith(b"DPAPI1\n"))
            self.assertNotIn(raw, encoded)
            self.assertEqual(store.read(1), record)
            self.assertEqual(store.read(2)["profiles"], [])
            self.assertIsNone(store.read(2)["active"])

    def test_credentials_are_scoped_to_positive_integer_user_id(self):
        with tempfile.TemporaryDirectory(prefix="zhiwei-store-test-") as directory:
            store = auth.CredentialStore(directory)
            for invalid in [None, 0, -1, True, "../user-1"]:
                with self.subTest(user=invalid), self.assertRaises(auth.ChatGPTError):
                    store.read(invalid)


class OAuthRouteTests(SignedIdentityMixin, unittest.TestCase):
    origin = "http://127.0.0.1"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zhiwei-oauth-test-")
        self.addCleanup(self.directory.cleanup)
        self.remote = patch("backend.chatgpt_auth._http", side_effect=self.fake_http).start()
        self.addCleanup(patch.stopall)
        patch("backend.providers._request_json", side_effect=AssertionError("No real service in OAuth tests")).start()
        self.app = create_app({"TESTING": True, "DATA_DIR": self.directory.name,
                               "SECRET_KEY": "synthetic-oauth-test-only", "RAG_PROVIDER": "chatgpt",
                               "EMBEDDING_PROVIDER": "ollama", "OLLAMA_EMBED_MODEL": "fake-embedding",
                               "AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""})
        self.client = self.app.test_client()
        self.user_id = self.register(self.client, "oauth_alice")
        self.store = self.app.extensions["chatgpt_store"]
        self.pending = self.app.extensions["chatgpt_pending"]
        self.token_response = None
        self.stream_factory = lambda: stream({"type": "response.output_text.delta", "delta": '{"ok":true}'},
                                              {"type": "response.completed", "response": {"status": "completed"}})

    def post(self, path, client=None, **kwargs):
        return (client or self.client).post(path, base_url=self.origin, **kwargs)

    def get(self, path, client=None, **kwargs):
        return (client or self.client).get(path, base_url=self.origin, **kwargs)

    def register(self, client, username):
        response = self.post("/api/auth/register", client, json={"username": username, "password": "synthetic-password"})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["user"]["id"]

    def fake_http(self, url, payload=None, bearer=None, form=False, stream=False):
        if url == auth.ISSUER + "/.well-known/jwks.json":
            return {"keys": [self.jwk]}
        if url == auth.TOKEN:
            self.assertTrue(form)
            self.assertEqual(payload["resource"], auth.RESOURCE)
            return dict(self.token_response)
        if url == auth.RESOURCE + "/models":
            return {"models": [{"slug": "synthetic-chat", "visibility": "list", "display_name": "Synthetic"}]}
        if url == auth.RESOURCE + "/responses":
            self.assertTrue(stream)
            self.assertFalse(payload["store"])
            self.assertTrue(payload["stream"])
            self.assertEqual(bearer, "synthetic-access-A")
            return self.stream_factory()
        raise AssertionError("Unexpected fake service request: " + url)

    def check_models(self, client=None):
        with patch("backend.providers._request_json", return_value={"models": [{"name": "fake-embedding"}]}), \
             patch("backend.providers.embed_texts", return_value=[[1.0, 0.0]]) as embedding:
            response = self.post("/api/models/check", client)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(embedding.called, "A catalog entry alone must not count as embedding inference.")
        return response.get_json()

    def connect(self, profile_id=None, client=None):
        response = self.post("/api/chatgpt/connect", client, json={"profile_id": profile_id} if profile_id else {})
        self.assertEqual(response.status_code, 200, response.get_json())
        parsed = urlsplit(response.get_json()["authorization_url"])
        self.assertEqual(parsed.scheme + "://" + parsed.netloc + parsed.path, auth.AUTHORIZE)
        return {key: values[0] for key, values in parse_qs(parsed.query).items()}

    def callback(self, parameters, client=None, **extra):
        query = {"state": parameters["state"], "code": "synthetic-code", "client_id": "synthetic-client"}
        query.update(extra)
        return self.get(auth.CALLBACK, client, query_string=query)

    def response_for(self, parameters, **changes):
        data = {"access_token": "synthetic-access-A", "refresh_token": "synthetic-refresh-A",
                "id_token": self.signed(nonce=parameters["nonce"]), "token_type": "Bearer",
                "expires_in": 3600, "scope": auth.SCOPES}
        data.update(changes)
        self.token_response = data
        return data

    def authorize(self, client=None, subject="subject-A"):
        parameters = self.connect(client=client)
        self.response_for(parameters, id_token=self.signed(nonce=parameters["nonce"], subject=subject))
        response = self.callback(parameters, client)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self.get("/api/chatgpt", client).get_json()["active_profile_id"]

    def test_every_membership_api_and_model_check_requires_local_login(self):
        anonymous = self.app.test_client()
        self.assertEqual(self.get("/api/chatgpt", anonymous).status_code, 401)
        for path in ["connect", "select", "model", "refresh-models", "disconnect"]:
            with self.subTest(path=path):
                self.assertEqual(self.post("/api/chatgpt/" + path, anonymous, json={}).status_code, 401)
        self.assertEqual(self.post("/api/models/check", anonymous).status_code, 401)
        self.remote.assert_not_called()

    def test_authorization_uses_pkce_nonce_resource_and_single_use_state(self):
        parameters = self.connect()
        attempt = self.pending[parameters["state"]]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt["verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(parameters["code_challenge"], challenge)
        self.assertEqual(parameters["code_challenge_method"], "S256")
        self.assertEqual(parameters["resource"], auth.RESOURCE)
        self.assertEqual(parameters["redirect_uri"], self.origin + auth.CALLBACK)
        self.assertNotEqual(parameters["nonce"], parameters["state"])
        self.response_for(parameters)
        self.assertEqual(self.callback(parameters).status_code, 200)
        self.assertNotIn(parameters["state"], self.pending)
        self.remote.reset_mock()
        self.assertEqual(self.callback(parameters).status_code, 400)
        self.remote.assert_not_called()

    def test_missing_unknown_expired_state_never_exchanges_code(self):
        parameters = self.connect()
        self.pending[parameters["state"]]["created"] = time.time() - 601
        for state in ["", "wrong-state", parameters["state"]]:
            with self.subTest(state=state):
                response = self.callback({"state": state})
                self.assertEqual(response.status_code, 400)
        self.remote.assert_not_called()
        self.assertFalse(self.store.read(self.user_id)["active"])

    def test_callback_bound_to_starting_local_user_and_not_consumed_by_stranger(self):
        parameters = self.connect()
        stranger = self.app.test_client()
        stranger_id = self.register(stranger, "oauth_bob")
        self.response_for(parameters)
        self.assertEqual(self.callback(parameters, stranger).status_code, 400)
        self.remote.assert_not_called()
        self.assertEqual(self.store.read(stranger_id)["profiles"], [])
        self.assertIn(parameters["state"], self.pending)
        self.assertEqual(self.callback(parameters).status_code, 200)
        self.assertTrue(self.get("/api/chatgpt").get_json()["connected"])
        self.assertFalse(self.get("/api/chatgpt", stranger).get_json()["connected"])

    def test_invalid_signed_identity_cannot_replace_connected_profile(self):
        active_id = self.authorize()
        before = self.store.read(self.user_id)
        for case in ["nonce", "issuer", "audience", "expired", "subject"]:
            with self.subTest(case=case):
                parameters = self.connect(profile_id=active_id)
                kwargs = {"nonce": parameters["nonce"]}
                kwargs.update({"nonce": "bad"} if case == "nonce" else
                              {"iss": "https://attacker.invalid"} if case == "issuer" else
                              {"aud": "other-client"} if case == "audience" else
                              {"exp": int(time.time()) - 120} if case == "expired" else {"subject": "subject-B"})
                self.response_for(parameters, id_token=self.signed(**kwargs), access_token="replacement-must-not-save")
                self.assertEqual(self.callback(parameters).status_code, 400)
                self.assertEqual(self.store.read(self.user_id), before)

    def test_reconnect_url_does_not_expose_existing_tokens(self):
        active_id = self.authorize()
        parameters = self.connect(profile_id=active_id)
        self.assertNotIn("id_token_hint", parameters)
        self.assertEqual(parameters["client_id"], "synthetic-client")
        encoded = json.dumps(parameters)
        for field in ["access_token", "refresh_token", "id_token"]:
            self.assertNotIn(self.token_response[field], encoded)

    def test_same_local_user_in_another_browser_cannot_finish_original_callback(self):
        parameters = self.connect()
        other_browser = self.app.test_client()
        response = self.post("/api/auth/login", other_browser,
                             json={"username": "oauth_alice", "password": "synthetic-password"})
        self.assertEqual(response.status_code, 200)
        self.response_for(parameters)
        self.assertEqual(self.callback(parameters, other_browser).status_code, 400)
        self.remote.assert_not_called()
        self.assertEqual(self.callback(parameters).status_code, 200)

    def test_refresh_with_different_signed_subject_cannot_replace_credentials(self):
        self.authorize()
        data = self.store.read(self.user_id)
        data["profiles"][0]["expires_at"] = time.time() - 60
        self.store.write(self.user_id, data)
        self.token_response = {"access_token": "replacement-must-not-save", "expires_in": 3600,
                               "scope": auth.SCOPES, "id_token": self.signed(subject="subject-B")}
        self.assertEqual(self.post("/api/chatgpt/refresh-models").status_code, 400)
        stored = self.store.read(self.user_id)
        self.assertNotIn("replacement-must-not-save", json.dumps(stored))
        self.assertEqual(stored["profiles"][0]["subject"], "subject-A")

    def test_transient_refresh_error_keeps_credentials_but_invalid_grant_clears_them(self):
        self.authorize()
        data = self.store.read(self.user_id)
        data["profiles"][0]["expires_at"] = time.time() - 60
        self.store.write(self.user_id, data)
        self.remote.side_effect = auth.ChatGPTError("模拟临时网络不可达")
        self.assertEqual(self.post("/api/chatgpt/refresh-models").status_code, 400)
        self.assertEqual(self.store.read(self.user_id), data)
        self.remote.side_effect = auth.InvalidGrant("模拟授权已失效，请重新登录")
        self.assertEqual(self.post("/api/chatgpt/refresh-models").status_code, 400)
        result = self.store.read(self.user_id)
        self.assertNotIn("access_token", result["profiles"][0])
        self.assertNotIn("refresh_token", result["profiles"][0])
        self.assertEqual(result["profiles"][0]["subject"], "subject-A")
        self.assertFalse(self.get("/api/chatgpt").get_json()["connected"])

    def test_disconnect_epoch_changed_during_code_exchange_prevents_token_revival(self):
        parameters = self.connect()
        self.response_for(parameters)

        def disconnect_during_exchange(url, *args, **kwargs):
            result = self.fake_http(url, *args, **kwargs)
            if url == auth.TOKEN:
                data = self.store.read(self.user_id)
                data["epoch"] += 1
                self.store.write(self.user_id, data)
            return result

        self.remote.side_effect = disconnect_during_exchange
        self.assertEqual(self.callback(parameters).status_code, 400)
        self.assertFalse(self.get("/api/chatgpt").get_json()["connected"])
        self.assertNotIn("synthetic-access-A", json.dumps(self.store.read(self.user_id)))

    def test_reconnect_cannot_change_registered_client(self):
        active_id = self.authorize()
        parameters = self.connect(profile_id=active_id)
        self.remote.reset_mock()
        self.assertEqual(self.callback(parameters, client_id="unrelated-client").status_code, 400)
        self.remote.assert_not_called()
        self.assertEqual(self.get("/api/chatgpt").get_json()["active_profile_id"], active_id)

    def test_missing_sharing_scopes_preserves_identity_but_blocks_model_use(self):
        parameters = self.connect()
        self.response_for(parameters, scope="openid profile email")
        self.assertEqual(self.callback(parameters).status_code, 200)
        status = self.get("/api/chatgpt").get_json()
        self.assertFalse(status["connected"])
        self.assertFalse(status.get("sharing"))
        self.assertEqual(self.store.read(self.user_id)["profiles"][0]["subject"], "subject-A")
        self.assertEqual(self.post("/api/chatgpt/refresh-models").status_code, 400)
        self.assertFalse(any(call.args[0].endswith("/models") for call in self.remote.call_args_list))

    def test_profile_selection_and_status_do_not_cross_local_users_or_return_credentials(self):
        alice_profile = self.authorize()
        stranger = self.app.test_client()
        stranger_id = self.register(stranger, "oauth_bob")
        bob_profile = self.authorize(stranger, subject="subject-B")
        self.assertNotEqual(alice_profile, bob_profile)
        self.assertEqual(self.post("/api/chatgpt/select", stranger, json={"profile_id": alice_profile}).status_code, 400)
        self.assertEqual(self.post("/api/chatgpt/connect", stranger, json={"profile_id": alice_profile}).status_code, 400)
        for client, expected, forbidden in [(self.client, alice_profile, bob_profile), (stranger, bob_profile, alice_profile)]:
            for response in [self.get("/api/chatgpt", client), self.get("/api/config", client),
                             self.post("/api/chatgpt/select", client, json={"profile_id": expected}),
                             self.post("/api/chatgpt/model", client, json={"model": "synthetic-chat"}),
                             self.post("/api/chatgpt/refresh-models", client)]:
                self.assertEqual(response.status_code, 200)
                encoded = response.get_data(as_text=True)
                self.assertNotIn(forbidden, encoded)
                for secret in ["synthetic-access-A", "synthetic-refresh-A", "access_token", "refresh_token", "id_token"]:
                    self.assertNotIn(secret, encoded)
        self.assertEqual(self.store.read(stranger_id)["active"], bob_profile)
        self.assertEqual(self.store.read(self.user_id)["active"], alice_profile)

    def test_disconnect_cancels_pending_callback_and_clears_only_current_user(self):
        self.authorize()
        stranger = self.app.test_client()
        self.register(stranger, "oauth_bob")
        self.authorize(stranger, subject="subject-B")
        parameters = self.connect()
        self.response_for(parameters)
        response = self.post("/api/chatgpt/disconnect")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["connected"])
        self.assertTrue(self.get("/api/chatgpt", stranger).get_json()["connected"])
        self.remote.reset_mock()
        self.assertEqual(self.callback(parameters).status_code, 400)
        self.remote.assert_not_called()
        self.assertFalse(self.get("/api/chatgpt").get_json()["connected"])

    def test_array_payloads_and_unlisted_model_have_controlled_errors(self):
        for path in ["connect", "select", "model"]:
            with self.subTest(path=path):
                self.assertEqual(self.post("/api/chatgpt/" + path, json=["invalid"]).status_code, 400)
        self.authorize()
        self.assertEqual(self.post("/api/chatgpt/model", json={"model": "invented-model"}).status_code, 400)
        self.assertEqual(self.get("/api/chatgpt").get_json()["selected_model"], "synthetic-chat")

    def test_authorization_origin_must_be_loopback_not_host_header_domain(self):
        # Match the login cookie host first, so authentication is not the reason for rejection.
        foreign = self.app.test_client()
        response = foreign.post("/api/auth/login", base_url="http://attacker.invalid",
                                json={"username": "oauth_alice", "password": "synthetic-password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(foreign.post("/api/chatgpt/connect", base_url="http://attacker.invalid", json={}).status_code, 400)
        self.remote.assert_not_called()

    def test_models_check_requires_completed_probe_and_get_status_never_calls_service(self):
        self.authorize()
        self.remote.reset_mock()
        status = self.get("/api/chatgpt").get_json()
        self.assertFalse(status["checked"])
        self.assertFalse(status["chat_ready"])
        self.assertFalse(self.get("/api/config").get_json()["rag"]["connected"])
        self.remote.assert_not_called()
        result = self.check_models()
        self.assertTrue(result["rag"]["chat_ready"])
        self.assertTrue(result["rag"]["embedding_ready"])
        self.assertTrue(result["rag"]["connected"])
        calls = [call for call in self.remote.call_args_list if call.args[0].endswith("/responses")]
        self.assertEqual(len(calls), 1)
        payload = calls[0].args[1]
        for unsupported in ["temperature", "max_output_tokens", "previous_response_id"]:
            self.assertNotIn(unsupported, payload)
        self.remote.reset_mock()
        self.assertTrue(self.get("/api/config").get_json()["rag"]["chat_ready"])
        self.assertTrue(self.get("/api/chatgpt").get_json()["checked"])
        self.remote.assert_not_called()

    def test_failed_incomplete_deltaonly_and_wrong_probe_json_clear_previous_success(self):
        self.authorize()
        self.assertTrue(self.check_models()["rag"]["chat_ready"])
        delta = {"type": "response.output_text.delta", "delta": '{"ok":true}'}
        for events in [[delta], [delta, {"type": "response.failed"}], [delta, {"type": "response.incomplete"}],
                       [{"type": "response.output_text.delta", "delta": '{"ok":1}'}, {"type": "response.completed"}]]:
            with self.subTest(events=events):
                self.stream_factory = lambda events=events: stream(*events)
                result = self.check_models()
                self.assertFalse(result["rag"]["connected"])
                self.assertFalse(result["rag"]["chat_ready"])
                self.assertFalse(self.get("/api/config").get_json()["rag"]["chat_ready"])

    def test_checked_membership_status_is_scoped_to_local_user_and_profile(self):
        first_profile = self.authorize()
        self.assertTrue(self.check_models()["rag"]["chat_ready"])
        stranger = self.app.test_client()
        self.register(stranger, "oauth_bob")
        self.authorize(stranger, subject="subject-B")
        self.remote.reset_mock()
        self.assertFalse(self.get("/api/config", stranger).get_json()["rag"]["chat_ready"])
        self.assertFalse(self.get("/api/config", stranger).get_json()["rag"]["embedding_ready"])
        self.assertTrue(self.get("/api/config").get_json()["rag"]["chat_ready"])
        self.remote.assert_not_called()
        second_profile = self.authorize(subject="subject-C")
        self.assertNotEqual(first_profile, second_profile)
        # Both account profiles advertise the same model, which must not share the checked cache.
        self.assertFalse(self.get("/api/config").get_json()["rag"]["chat_ready"])
        self.assertTrue(self.check_models()["rag"]["chat_ready"])
        self.assertEqual(self.post("/api/chatgpt/select", json={"profile_id": first_profile}).status_code, 200)
        self.assertFalse(self.get("/api/config").get_json()["rag"]["connected"])

    def test_connection_changed_during_stream_cannot_produce_successful_check(self):
        self.authorize()

        def changed_during_response():
            data = self.store.read(self.user_id)
            data["epoch"] += 1
            self.store.write(self.user_id, data)
            return stream({"type": "response.output_text.delta", "delta": '{"ok":true}'},
                          {"type": "response.completed"})

        self.stream_factory = changed_during_response
        result = self.check_models()
        self.assertFalse(result["rag"]["chat_ready"])
        self.assertFalse(result["rag"]["connected"])


if __name__ == "__main__":
    unittest.main()
