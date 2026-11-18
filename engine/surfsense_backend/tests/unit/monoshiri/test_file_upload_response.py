"""The fileupload route's dict is now typed; it must validate as the model."""

from app.schemas.documents import FileUploadResponse


def test_file_upload_response_accepts_route_payload():
    # The exact dict returned by create_documents_file_upload.
    payload = {
        "message": "Files uploaded for processing",
        "document_ids": [1, 2],
        "duplicate_document_ids": [3],
        "total_files": 3,
        "pending_files": 2,
        "skipped_duplicates": 1,
    }

    model = FileUploadResponse.model_validate(payload)

    assert model.model_dump() == payload
