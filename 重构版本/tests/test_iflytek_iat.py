from io import BytesIO

from robot_modbus_lite.iflytek_iat import IFlytekIATClient


def test_transcribe_file_streams_from_memory_and_reports_chunks(tmp_path):
    audio_path = tmp_path / "sample.pcm"
    audio_path.write_bytes(b"pcm-bytes")
    stream_types = []

    class FakeSdkClient:
        def stream(self, audio_stream):
            stream_types.append(type(audio_stream))
            assert audio_stream.read(3) == b"pcm"
            yield {"result": {"ws": [{"cw": [{"w": "你"}]}]}}
            yield {"result": {"ws": [{"cw": [{"w": "好"}]}]}}

    client = IFlytekIATClient.__new__(IFlytekIATClient)
    client._use_proxy = False
    client._client = FakeSdkClient()

    partials = []
    result = client.transcribe_file(str(audio_path), chunk_callback=partials.append)

    assert stream_types == [BytesIO]
    assert partials == ["你", "你好"]
    assert result.text == "你好"


def test_transcribe_microphone_reports_chunks():
    class FakeStream:
        def stop_stream(self):
            pass

        def close(self):
            pass

    class FakeSdkClient:
        def stream(self, audio_stream):
            assert isinstance(audio_stream, FakeStream)
            yield {"result": {"ws": [{"cw": [{"w": "小"}]}]}}
            yield {"result": {"ws": [{"cw": [{"w": "正"}]}]}}

    client = IFlytekIATClient.__new__(IFlytekIATClient)
    client._use_proxy = False
    client._client = FakeSdkClient()
    client._open_microphone_stream = lambda _config: FakeStream()

    partials = []
    result = client.transcribe_microphone(chunk_callback=partials.append)

    assert partials == ["小", "小正"]
    assert result.text == "小正"
