"""Supplementary exact-boundary checks; no project/user database is modified."""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from backend import create_app


def padded_valid_pdf(target_bytes: int) -> bytes:
    """Pad harmless inter-object whitespace; keep real xref offsets correct."""
    stream = b"BT /F1 12 Tf 72 720 Td (Exact boundary document.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")

    def trailer(padding):
        tail = bytearray(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            tail.extend(f"{offset:010d} 00000 n \n".encode())
        tail.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{len(body) + padding}\n%%EOF\n".encode())
        return tail

    padding = target_bytes - len(body) - len(trailer(0))
    for _ in range(4):
        padding = target_bytes - len(body) - len(trailer(padding))
    payload = bytes(body) + b" " * padding + bytes(trailer(padding))
    assert len(payload) == target_bytes
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 14 isolated exact-boundary checks for Zhiwei.")
    parser.add_argument("--output", type=Path, help="JSON output path; defaults to a new file in the operating-system temporary directory.")
    args = parser.parse_args()
    results = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": sys.version.split()[0],
        "environment": "Flask test client; isolated TemporaryDirectory; external model disabled",
        "scope": "Supplementary boundary checks, separate from the unittest suite",
        "cases": [],
    }


    def record(name, expected, observed, **details):
        passed = expected == observed
        results["cases"].append({"case": name, "expected": expected, "observed": observed, "passed": passed, **details})
        print(name, "PASS" if passed else "FAIL", observed, flush=True)


    with tempfile.TemporaryDirectory(prefix="zhiwei-exact-boundaries-") as directory:
        app = create_app({"TESTING": True, "DATA_DIR": directory, "SECRET_KEY": "temporary-boundary-test-secret",
                          "RAG_PROVIDER": "local", "EMBEDDING_PROVIDER": "",
                          "AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""})
        client = app.test_client()
        assert client.post("/api/auth/register", json={"username": "boundary_tester", "password": "temporary-pass-2026"}).status_code == 201

        def library(name):
            response = client.post("/api/libraries", json={"name": name})
            assert response.status_code == 201
            return response.json["library"]["id"]

        def upload(library_id, files):
            return client.post(f"/api/libraries/{library_id}/documents",
                               data={"files": [(io.BytesIO(payload), name) for name, payload in files]},
                               content_type="multipart/form-data")

        batch_library = library("Exact batch boundary")
        response = upload(batch_library, [(f"unique-{index}.txt", f"Distinct valid source number {index}.".encode()) for index in range(10)])
        record("batch_10_distinct_valid_files", {"status": 201, "documents": 10, "errors": 0},
               {"status": response.status_code, "documents": len(response.json.get("documents", [])), "errors": len(response.json.get("errors", []))})
        response = upload(batch_library, [(f"eleven-{index}.txt", f"Different source number {index}.".encode()) for index in range(11)])
        record("batch_11_files_rejected", {"status": 400, "persisted_documents": 10},
               {"status": response.status_code, "persisted_documents": len(client.get(f"/api/libraries/{batch_library}/documents").json["documents"])},
               message=response.json.get("error"))

        size_library = library("Actual byte boundary")
        payload = padded_valid_pdf(20 * 1024 * 1024)
        response = upload(size_library, [("exact-20MiB.pdf", payload)])
        documents = response.json.get("documents", [])
        extracted = ""
        if documents:
            chunks = client.get(f"/api/documents/{documents[0]['id']}/chunks").json["chunks"]
            extracted = "\n".join(chunk["content"] for chunk in chunks)
        record("actual_20MiB_valid_pdf_accepted", {"status": 201, "documents": 1, "stored_bytes": 20971520, "text_extracted": True},
               {"status": response.status_code, "documents": len(documents), "stored_bytes": documents[0]["size"] if documents else None,
                "text_extracted": "Exact boundary document." in extracted},
               uploaded_bytes=len(payload), fixture="Single text-page PDF with harmless whitespace before xref; accurate offsets; no compressed large stream",
               extracted_characters=len(extracted), errors=response.json.get("errors", []))
        response = upload(size_library, [("over-20MiB.pdf", payload + b" ")])
        record("actual_20MiB_plus_1_byte_rejected", {"status": 400, "documents": 0, "persisted_documents": 1},
               {"status": response.status_code, "documents": len(response.json.get("documents", [])),
                "persisted_documents": len(client.get(f"/api/libraries/{size_library}/documents").json["documents"])},
               uploaded_bytes=len(payload) + 1, errors=response.json.get("errors", []))
        del payload

        for length, expected in [(2, 201), (32, 201), (1, 400), (33, 400)]:
            separate = app.test_client()
            response = separate.post("/api/auth/register", json={"username": "u" * length, "password": "temporary-pass-2026"})
            record(f"username_length_{length}", expected, response.status_code,
                   input_kind="ASCII letters", message=response.json.get("error"))
        for length, expected in [(8, 201), (128, 201), (7, 400), (129, 400)]:
            separate = app.test_client()
            response = separate.post("/api/auth/register", json={"username": f"password_case_{length}", "password": "p" * length})
            record(f"password_length_{length}", expected, response.status_code,
                   input_kind="ASCII characters", message=response.json.get("error"))

        empty_library = library("Empty question boundary")
        for length, expected_status, expected_messages in [(2000, 200, 2), (2001, 400, 0)]:
            chat = client.post(f"/api/libraries/{empty_library}/sessions", json={}).json["session"]["id"]
            response = client.post(f"/api/sessions/{chat}/messages", json={"question": "z" * length})
            history = client.get(f"/api/sessions/{chat}").json["messages"]
            observed = {"status": response.status_code, "persisted_messages": len(history)}
            expected = {"status": expected_status, "persisted_messages": expected_messages}
            if length == 2000:
                expected["mode"] = "refusal"
                observed["mode"] = response.json.get("assistant_message", {}).get("mode")
            record(f"question_length_{length}_empty_library", expected, observed,
                   message=response.json.get("error"))

    results["temporary_directory_cleanup_succeeded"] = True
    results["passed"] = sum(case["passed"] for case in results["cases"])
    results["total"] = len(results["cases"])
    results["all_passed"] = results["passed"] == results["total"]
    serialized = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized, encoding="utf-8")
    else:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="zhiwei-boundary-results-", suffix=".json", delete=False) as result_file:
            result_file.write(serialized)
            destination = Path(result_file.name)
    print(f"Saved {results['passed']}/{results['total']} checks to {destination}", flush=True)
    return 0 if results["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
