from app.services.decoder import decode_video_frame
from tests.fixtures.camera import make_mock_av_frame


class TestDecodeVideoFrame:
    def test_returns_correct_width(self) -> None:
        av_frame = make_mock_av_frame(width=1280, height=720)
        frame = decode_video_frame(av_frame, sequence=0)
        assert frame.width == 1280

    def test_returns_correct_height(self) -> None:
        av_frame = make_mock_av_frame(width=1280, height=720)
        frame = decode_video_frame(av_frame, sequence=0)
        assert frame.height == 720

    def test_reformats_to_rgb24(self) -> None:
        av_frame = make_mock_av_frame()
        decode_video_frame(av_frame, sequence=0)
        av_frame.reformat.assert_called_once_with(format="rgb24")

    def test_sequence_is_propagated(self) -> None:
        av_frame = make_mock_av_frame()
        frame = decode_video_frame(av_frame, sequence=42)
        assert frame.sequence == 42

    def test_sequential_sequences_are_independent(self) -> None:
        av_frame = make_mock_av_frame()
        f1 = decode_video_frame(av_frame, sequence=5)
        f2 = decode_video_frame(av_frame, sequence=6)
        assert f1.sequence == 5
        assert f2.sequence == 6

    def test_timestamp_computed_from_pts_and_time_base(self) -> None:
        # pts=90000, time_base=1/90000 → timestamp=1.0 second
        av_frame = make_mock_av_frame(pts=90000, time_base=1 / 90000)
        frame = decode_video_frame(av_frame, sequence=0)
        assert abs(frame.timestamp - 1.0) < 1e-6

    def test_timestamp_fallback_when_pts_is_none(self) -> None:
        av_frame = make_mock_av_frame(pts=None)
        frame = decode_video_frame(av_frame, sequence=0)
        assert frame.timestamp > 0.0

    def test_image_is_read_only(self) -> None:
        av_frame = make_mock_av_frame()
        frame = decode_video_frame(av_frame, sequence=0)
        assert not frame.image.flags.writeable

    def test_image_shape_matches_dimensions(self) -> None:
        av_frame = make_mock_av_frame(width=320, height=240)
        frame = decode_video_frame(av_frame, sequence=0)
        assert frame.image.shape == (240, 320, 3)
