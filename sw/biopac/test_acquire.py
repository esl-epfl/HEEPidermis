"""No hardware required: python3 -m unittest discover -s sw/biopac -p 'test_*.py'."""

import csv
import io
import socket
import struct
import unittest
from unittest.mock import Mock, patch

from acquire import (AcquisitionError, Capture, LiveBlock, MP160, READ_BYTES, VOLTS_PER_COUNT,
                     csv_rows, decode_reply, frame, identification, timer_payload)


def reply(payload):
    body = b"\x01" + payload
    return body + bytes((-sum(body) & 255,))


class ProtocolTests(unittest.TestCase):
    def test_frames_match_physical_device_requests(self):
        self.assertEqual(frame(0x07), bytes.fromhex("01 07 f8"))
        self.assertEqual(frame(0xA9, b"\x01\0\0\0\0"), bytes.fromhex("01 a9 01 00 00 00 00 55"))
        self.assertEqual(frame(0x93, struct.pack(">IH", 0, 62)), bytes.fromhex("01 93 00 00 00 00 00 3e 2e"))

    def test_reply_validation(self):
        self.assertEqual(decode_reply(bytes.fromhex("01 06 f9"), 1), b"\x06")
        for bad, expected in ((b"\x01\x06", 1), (b"\x01\x06\xf8", 1),
                              (b"\x02\x06\xf8", 1), (b"\x01\x06\xf9\0", 1)):
            with self.subTest(packet=bad), self.assertRaises(AcquisitionError):
                decode_reply(bad, expected)

    def test_identification_and_self_broadcast(self):
        packet = b"iDnt" + bytes.fromhex("00 90 3d 00 24 b5") + b"2107A-00024B5".ljust(32, b"\0")
        self.assertEqual(identification(packet, ("169.254.164.181", 16005)), {
            "ip": "169.254.164.181", "mac": "00:90:3d:00:24:b5", "serial": "2107A-00024B5",
        })
        for invalid in (b"iDnt", packet[:-1], packet + b"\0", b"xxxx" + packet[4:],
                        packet[:10] + b"\xff" * 32):
            self.assertIsNone(identification(invalid, ("169.254.164.181", 16005)))
        self.assertIsNone(identification(packet, ("169.254.164.181", 16004)))

    def test_timer_encoding(self):
        self.assertEqual(timer_payload(100), bytes.fromhex("00 00 00 9c 3e") * 3)
        for rate in (100, 200, 500, 1000):
            timers = timer_payload(rate)
            reload = struct.unpack(">H", timers[3:5])[0]
            self.assertEqual(4_000_000 / (reload + 2), rate)
            self.assertEqual(timers[:5], timers[5:10])
        with self.assertRaises(ValueError):
            timer_payload(50)


class ConversionTests(unittest.TestCase):
    def test_signed_counts_zero_and_no_rectification(self):
        capture = Capture(struct.pack(">hhhh", -32768, -128, 0, 32767), 3, 100, 0.04, 0.04, "test", False)
        rows = list(csv_rows(capture, 2, 0))
        self.assertEqual([row[3] for row in rows], [-32768, -128, 0, 32767])
        self.assertEqual([row[1] for row in rows], [0, 0.01, 0.02, 0.03])
        self.assertAlmostEqual(rows[1][4], -128 * VOLTS_PER_COUNT)
        self.assertLess(rows[1][5], 0)
        self.assertEqual([row[6] for row in rows], [1, 0, 0, 1])

    def test_calibrated_offset_does_not_change_raw_voltage(self):
        capture = Capture(struct.pack(">h", 1000), 3, 100, 1, 1, "test", False)
        row = next(csv_rows(capture, 2, 0.1))
        self.assertEqual(row[4], 1000 * VOLTS_PER_COUNT)
        self.assertAlmostEqual(row[5], 2 * (row[4] - 0.1))
        file = io.StringIO()
        csv.writer(file).writerows([row])
        self.assertIn("1000", file.getvalue())


class RecorderTests(unittest.TestCase):
    def setUp(self):
        factory = patch("acquire.socket.socket")
        self.addCleanup(factory.stop)
        self.socket = factory.start().return_value
        self.events = []
        self.client = MP160("test0", "169.254.164.181", self.events.append)
        self.addCleanup(self.client.close)

    def test_socket_is_interface_scoped_and_exclusive(self):
        self.socket.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"test0\0")
        self.socket.bind.assert_called_once_with(("0.0.0.0", 16004))
        self.socket.connect.assert_called_once_with(("169.254.164.181", 16004))

    def test_exchange_and_trace(self):
        self.socket.recv.return_value = reply(b"\x80")
        self.assertEqual(self.client.status(), 0x80)
        self.socket.send.assert_called_once_with(frame(7))
        self.assertEqual(self.events[0]["rx_hex"], "01807f")

    def test_timeout_is_not_retried(self):
        self.socket.recv.side_effect = socket.timeout("timed out")
        with self.assertRaisesRegex(AcquisitionError, "0x07"):
            self.client.status()
        self.socket.send.assert_called_once()
        self.assertIn("error", self.events[0])

    def test_nack_is_rejected(self):
        self.socket.recv.return_value = reply(b"\x15")
        with self.assertRaisesRegex(AcquisitionError, "rejected"):
            self.client._set(0x12)

    def memory(self, raw, positions=None):
        self.client._position = Mock(side_effect=positions) if positions else Mock(return_value=(len(raw), 0))
        self.client._read = Mock(side_effect=lambda offset, length: raw[offset:offset + length])

    def test_chunked_frozen_buffer_uses_byte_offsets(self):
        raw = struct.pack(">2001h", *range(-1000, 1001))
        self.memory(raw)
        self.assertEqual(self.client._frozen_buffer(10000), raw)
        calls = self.client._read.call_args_list
        self.assertEqual(calls[0].args, (0, READ_BYTES))
        self.assertEqual(calls[1].args, (READ_BYTES, READ_BYTES))
        self.assertEqual(calls[2].args, (2 * READ_BYTES, len(raw) - 2 * READ_BYTES))

    def test_invalid_or_wrapped_buffers_are_not_exported(self):
        for position in ((0, 0), (3, 0), (20, 1), (10001, 0)):
            self.memory(b"1234", [position, position])
            with self.subTest(position=position), self.assertRaises(AcquisitionError):
                self.client._frozen_buffer(10000)
            self.client._read.assert_not_called()

    def test_counter_must_stop_changing(self):
        self.memory(b"1234", [(2, 0), (4, 0), (6, 0), (8, 0)])
        with self.assertRaisesRegex(AcquisitionError, "still changing"):
            self.client._frozen_buffer(10000)

    def test_repeated_read_detects_changing_memory(self):
        self.memory(b"1234")
        self.client._read.side_effect = [b"1234", b"xxxx"]
        with self.assertRaisesRegex(AcquisitionError, "memory changed"):
            self.client._frozen_buffer(10000)

    def prepared(self):
        self.client.status = Mock(side_effect=[0x80, 0x80])
        self.client._set = Mock()
        self.client._frozen_buffer = Mock(return_value=struct.pack(">hh", -128, 32))

    @patch("acquire.time.sleep")
    def test_lifecycle_stops_adc_not_only_stimulation(self, sleep):
        self.prepared()
        result = self.client.capture(1, 3, 100, 10000)
        self.assertEqual(result.count, 2)
        self.assertFalse(result.interrupted)
        commands = [call.args[0] for call in self.client._set.call_args_list]
        self.assertEqual(commands, [0xA9, 0x9C, 0x85, 0x06, 0x89, 0x82, 0x12])
        self.assertEqual(self.client._set.call_args_list[-2].args[1], bytes.fromhex("03 00 00 04 00 00"))

    @patch("acquire.time.sleep")
    def test_one_based_channels_use_same_mask_in_batch_and_live(self, sleep):
        # Explicit boundary and adjacent-channel expectations catch off-by-one
        # and byte-order errors without reproducing the implementation's shift.
        for channel, mask in ((1, "00 01"), (3, "00 04"), (8, "00 80"), (9, "01 00"), (16, "80 00")):
            for live in (False, True):
                with self.subTest(channel=channel, live=live):
                    self.prepared()
                    if live:
                        self.client.start_live(channel, 100, 10000)
                        self.client.finish_live()
                    else:
                        self.client.capture(1, channel, 100, 10000)
                    starts = [call.args[1] for call in self.client._set.call_args_list if call.args[0] == 0x82]
                    self.assertEqual(starts, [bytes.fromhex(f"03 00 {mask} 00 00")])
                    if channel == 9:
                        self.assertEqual(frame(0x82, starts[0]), bytes.fromhex("01 82 03 00 01 00 00 00 79"))
                    self.client._set.assert_called_with(0x12)

    def test_invalid_live_channel_is_rejected_before_any_device_command(self):
        for channel in (0, 17, -1):
            self.prepared()
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                self.client.start_live(channel, 100, 10000)
            self.client.status.assert_not_called()
            self.client._set.assert_not_called()

    @patch("acquire.time.sleep", side_effect=KeyboardInterrupt)
    def test_interrupt_stops_and_returns_partial_capture(self, sleep):
        self.prepared()
        self.assertTrue(self.client.capture(1, 3, 100, 10000).interrupted)
        self.client._set.assert_called_with(0x12)

    def test_lost_start_ack_still_attempts_stop(self):
        self.prepared()

        def set_command(command, payload=b""):
            if command == 0x82:
                raise AcquisitionError("lost ACK")

        self.client._set.side_effect = set_command
        with self.assertRaisesRegex(AcquisitionError, "lost ACK"):
            self.client.capture(1, 3, 100, 10000)
        self.client._set.assert_called_with(0x12)
        self.client._frozen_buffer.assert_not_called()

    def test_existing_acquisition_is_not_interrupted(self):
        self.prepared()
        self.client.status.return_value = 0x81
        self.client.status.side_effect = None
        with self.assertRaisesRegex(AcquisitionError, "already acquiring"):
            self.client.capture(1, 3, 100, 10000)
        self.client._set.assert_not_called()

    def test_invalid_configuration_never_changes_hardware(self):
        for seconds, channel, rate, capacity in ((float("nan"), 3, 100, 10000),
                (0, 3, 100, 10000), (1, 0, 100, 10000), (1, 17, 100, 10000),
                (1, 3, 50, 10000), (100, 3, 1000, 10000)):
            self.prepared()
            with self.subTest(seconds=seconds, channel=channel, rate=rate), self.assertRaises(ValueError):
                self.client.capture(seconds, channel, rate, capacity)
            self.client._set.assert_not_called()

    def test_live_requires_an_owned_acquisition(self):
        with self.assertRaisesRegex(AcquisitionError, "No live acquisition"):
            self.client.read_live()
        self.assertEqual(self.client.finish_live(), LiveBlock(0, b""))
        self.socket.send.assert_not_called()

    def test_live_start_and_even_word_streaming(self):
        self.prepared()
        self.client.start_live(3, 100, 10000)
        self.memory(b"\x00\x01\x00\x02\x00\x03", [(5, 0), (6, 0)])
        self.assertEqual(self.client.read_live(), LiveBlock(0, b"\x00\x01\x00\x02"))
        self.assertEqual(self.client.read_live(), LiveBlock(2, b"\x00\x03"))
        self.assertEqual(self.client._read.call_args_list[-1].args, (4, 2))
        with self.assertRaisesRegex(AcquisitionError, "already owns"):
            self.client.start_live(3, 100, 10000)

    def test_live_poll_work_is_bounded(self):
        self.prepared()
        self.client.start_live(3, 100, 100000)
        self.memory(bytes(40000))
        block = self.client.read_live()
        self.assertEqual(len(block.raw), 8 * READ_BYTES)
        self.assertEqual(self.client._read.call_count, 8)

    def test_live_wrap_or_backwards_counter_is_rejected(self):
        self.client._live_raw = bytearray(b"1234")
        self.client._live_capacity = 10000
        for position in ((2, 0), (6, 1), (10002, 0)):
            self.memory(bytes(6), [position])
            with self.subTest(position=position), self.assertRaises(AcquisitionError):
                self.client.read_live()
            self.client._read.assert_not_called()

    def test_live_finish_stops_verifies_and_returns_only_tail(self):
        self.prepared()
        self.client.start_live(3, 100, 10000)
        self.client._live_raw.extend(b"\x00\x01")
        self.client._frozen_buffer.return_value = b"\x00\x01\x00\x02\x00\x03"
        self.assertEqual(self.client.finish_live(), LiveBlock(1, b"\x00\x02\x00\x03"))
        self.client._set.assert_called_with(0x12)
        self.assertIsNone(self.client._live_raw)
        self.assertEqual(self.client.finish_live(), LiveBlock(0, b""))

    def test_live_mismatch_is_not_verified_or_replaced_silently(self):
        self.prepared()
        self.client.start_live(3, 100, 10000)
        self.client._live_raw.extend(b"\x00\x04")
        with self.assertRaisesRegex(AcquisitionError, "do not match"):
            self.client.finish_live()
        self.assertIsNone(self.client._live_raw)
        self.client._set.assert_called_with(0x12)

    def test_live_lost_start_ack_still_stops(self):
        self.prepared()

        def set_command(command, payload=b""):
            if command == 0x82:
                raise AcquisitionError("lost live ACK")

        self.client._set.side_effect = set_command
        with self.assertRaisesRegex(AcquisitionError, "lost live ACK"):
            self.client.start_live(3, 100, 10000)
        self.client._set.assert_called_with(0x12)
        self.assertIsNone(self.client._live_raw)

    def test_live_does_not_stop_an_external_recording(self):
        self.prepared()
        self.client.status.side_effect = None
        self.client.status.return_value = 0x81
        with self.assertRaisesRegex(AcquisitionError, "already acquiring"):
            self.client.start_live(3, 100, 10000)
        self.client._set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
