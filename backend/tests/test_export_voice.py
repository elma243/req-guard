"""Voice notes in uploaded exports: WhatsApp "with media" ZIPs, Messenger exports and ZIPs. ZIPs are built here."""

import io
import json
import zipfile

import pytest

from app.connectors import archive
from app.connectors.files import connector_for_upload, detect_type
from app.connectors.messenger_export import MessengerExportConnector, MessengerZipConnector
from app.connectors.whatsapp_export import WhatsAppExportConnector, WhatsAppZipConnector

ANDROID_CHAT = (
    "12/03/24, 14:00 - Sarah Khan: We need a login page\n"
    "12/03/24, 14:05 - Sarah Khan: PTT-20240312-WA0003.opus (file attached)\n"
    "12/03/24, 14:06 - Rahim Uddin: IMG-20240312-WA0004.jpg (file attached)\n"
    "Here is the mockup\n"
    "12/03/24, 14:07 - Sarah Khan: PTT-20240312-WA0009.opus (file attached)\n"
)
IOS_CHAT = (
    "[12/03/24, 2:00:00 PM] Sarah Khan: Order report lagbe\n"
    "[12/03/24, 2:05:12 PM] Sarah Khan: \u200e<attached: 00000012-AUDIO-2024-03-12-14-05-12.opus>\n"
    "[12/03/24, 2:06:00 PM] Sarah Khan: \u200e<attached: 00000013-PHOTO-2024-03-12-14-06-00.jpg>\n"
)


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def voices(messages):
    return [m for m in messages if m.kind == "voice"]


# ---------- WhatsApp ----------


def test_android_zip_links_audio_files_to_their_messages():
    data = make_zip({
        "WhatsApp Chat with Acme.txt": ANDROID_CHAT.encode(),
        "PTT-20240312-WA0003.opus": b"fake-opus",
        "IMG-20240312-WA0004.jpg": b"jpg",
    })
    assert detect_type("WhatsApp Chat with Acme.zip", data) == "whatsapp_zip"
    connector = connector_for_upload("WhatsApp Chat with Acme.zip", data)
    assert isinstance(connector, WhatsAppZipConnector)
    messages = connector.fetch()

    assert [m.text for m in messages if m.kind == "text"] == ["We need a login page", "Here is the mockup"]
    found, missing = voices(messages)
    assert (found.channel, found.author, found.audio.filename) == ("Acme", "Sarah Khan", "PTT-20240312-WA0003.opus")
    assert (found.audio.data, found.audio.mime_type, found.audio.skip_reason) == (b"fake-opus", "audio/ogg", None)
    assert found.raw["line"] == 2
    assert missing.audio.data is None and "not in the ZIP" in missing.audio.skip_reason
    # The same ZIP again gives the same ids (no duplicates on re-upload)
    assert [m.external_id for m in connector_for_upload("x.zip", data).fetch()] == [m.external_id for m in messages]


def test_ios_zip_uses_the_zip_name_for_the_chat():
    data = make_zip({"_chat.txt": IOS_CHAT.encode(), "00000012-AUDIO-2024-03-12-14-05-12.opus": b"fake"})
    messages = connector_for_upload("WhatsApp Chat - Acme Logistics.zip", data).fetch()
    (voice,) = voices(messages)
    assert voice.channel == "Acme Logistics"
    assert voice.audio.data == b"fake"
    assert [m.text for m in messages if m.kind == "text"] == ["Order report lagbe"]  # the photo is skipped


def test_txt_alone_keeps_the_voice_note_with_a_reason():
    connector = WhatsAppExportConnector(ANDROID_CHAT.encode(), "WhatsApp Chat with Acme.txt")
    found = voices(connector.fetch())
    assert len(found) == 2
    assert all('with media' in m.audio.skip_reason for m in found)


def test_large_audio_in_the_zip_is_not_read(monkeypatch):
    monkeypatch.setattr(archive, "MAX_AUDIO_BYTES", 4)
    data = make_zip({"WhatsApp Chat with Acme.txt": ANDROID_CHAT.encode(), "PTT-20240312-WA0003.opus": b"too-big"})
    voice = voices(connector_for_upload("a.zip", data).fetch())[0]
    assert voice.audio.data is None and voice.audio.size == 7  # the importer marks it too large


def test_audio_total_per_zip_is_limited(monkeypatch):
    monkeypatch.setattr(archive, "MAX_AUDIO_TOTAL", 10)
    data = make_zip({
        "WhatsApp Chat with Acme.txt": ANDROID_CHAT.encode(),
        "PTT-20240312-WA0003.opus": b"12345678",
        "PTT-20240312-WA0009.opus": b"12345678",
    })
    first, second = voices(connector_for_upload("a.zip", data).fetch())
    assert first.audio.data == b"12345678"
    assert second.audio.data is None and "100 MB" in second.audio.skip_reason


def test_zip_without_a_chat_is_not_supported():
    assert detect_type("photos.zip", make_zip({"a.jpg": b"x"})) is None
    assert detect_type("broken.zip", b"not a zip") is None


# ---------- Messenger ----------


def messenger_json(uri: str = "messages/inbox/karim_123/audio/audioclip1729000000000.mp4") -> dict:
    return {
        "participants": [{"name": "Karim"}, {"name": "Rina"}],
        "title": "Karim",
        "thread_path": "inbox/karim_123",
        "messages": [
            {"sender_name": "Karim", "timestamp_ms": 1729000000000, "content": "Karim sent a voice message.",
             "audio_files": [{"uri": uri, "creation_timestamp": 1729000000}]},
            {"sender_name": "Karim", "timestamp_ms": 1728999000000, "content": "Payment with bKash lagbe"},
        ],
    }


def test_messenger_zip_reads_audio_files():
    data = make_zip({
        "your_activity_across_facebook/messages/inbox/karim_123/message_1.json": json.dumps(messenger_json()).encode(),
        "your_activity_across_facebook/messages/inbox/karim_123/audio/audioclip1729000000000.mp4": b"fake-mp4",
    })
    assert detect_type("facebook-export.zip", data) == "messenger_zip"
    connector = connector_for_upload("facebook-export.zip", data)
    assert isinstance(connector, MessengerZipConnector)
    messages = connector.fetch()
    assert [m.kind for m in messages] == ["text", "voice"]
    voice = messages[1]
    assert (voice.audio.filename, voice.audio.mime_type, voice.audio.data) == (
        "audioclip1729000000000.mp4", "audio/mp4", b"fake-mp4"
    )
    assert voice.author == "Karim"


def test_messenger_json_alone_keeps_the_voice_note_with_a_reason():
    connector = MessengerExportConnector(json.dumps(messenger_json()).encode(), "message_1.json")
    (voice,) = voices(connector.fetch())
    assert "as a ZIP" in voice.audio.skip_reason


def test_messenger_newer_layout_media_list():
    data = {
        "participants": ["Karim", "Rina"],
        "threadName": "Karim",
        "messages": [{"senderName": "Karim", "timestamp": 1729000000000, "text": "",
                      "media": [{"uri": "./media/voice_1.m4a"}, {"uri": "./media/photo.jpg"}]}],
    }
    (voice,) = voices(MessengerExportConnector(json.dumps(data).encode(), "chat.json").fetch())
    assert voice.audio.filename == "voice_1.m4a"


@pytest.mark.parametrize("name", ["../../etc/passwd.opus", "C:\\\\evil\\\\PTT-1.opus"])
def test_zip_media_matches_names_only(name):
    media = archive.ZipMedia(zipfile.ZipFile(io.BytesIO(make_zip({"x/PTT-1.opus": b"a", "passwd.opus": b"b"}))))
    attachment = media.attachment(name, "missing")
    assert attachment.filename in {"passwd.opus", "PTT-1.opus"}
    assert attachment.data in {b"a", b"b"}
