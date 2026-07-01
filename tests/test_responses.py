from titiler_covjson.responses import COVJSON_MEDIA_TYPE, CovJSONResponse


def test_coveragejson_response_media_type() -> None:
    assert COVJSON_MEDIA_TYPE == "application/prs.coverage+json"
    resp = CovJSONResponse(content='{"type":"Coverage"}')
    assert resp.media_type == COVJSON_MEDIA_TYPE
