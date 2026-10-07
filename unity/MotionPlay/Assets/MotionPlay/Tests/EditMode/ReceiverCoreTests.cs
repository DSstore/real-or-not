using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using MotionPlay.Networking;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MotionPlay.Tests
{
    public static class PacketFixture
    {
        public const string StreamA = "a1e111a1-1111-4111-8111-111111111111";
        public const string StreamB = "b2e222b2-2222-4222-8222-222222222222";
        public static JObject Json(long sequence = 0, string stream = StreamA, bool tracking = true)
        {
            return new JObject
            {
                ["type"] = "CV_STATE", ["version"] = 1, ["stream_id"] = stream,
                ["sequence"] = sequence, ["timestamp"] = 1770000000123L,
                ["hand"] = "right", ["tracking"] = tracking,
                ["position"] = tracking ? (JToken)new JObject { ["x"] = 0.55, ["y"] = 0.31, ["z"] = -0.03 } : JValue.CreateNull(),
                ["gesture"] = tracking ? "OPEN_HAND" : "UNKNOWN",
                ["confidence"] = tracking ? 0.95 : 0.0, ["mirrored"] = true
            };
        }

        public static byte[] Bytes(JObject json) => Encoding.UTF8.GetBytes(json.ToString());
        public static CvState State(long sequence = 0, string stream = StreamA, bool tracking = true)
        {
            byte[] bytes = Bytes(Json(sequence, stream, tracking));
            Assert.IsTrue(CvStateCodec.TryDecode(bytes, bytes.Length, out CvState state));
            return state;
        }
    }

    public sealed class CvStateCodecTests
    {
        [Test]
        public void TrackedAndLostPacketsHaveExactSemanticValues()
        {
            var state = PacketFixture.State();
            Assert.IsTrue(state.Tracking);
            Assert.AreEqual(0.55, state.Position.X);
            Assert.AreEqual(-0.03, state.Position.Z);
            Assert.AreEqual(1770000000123L, state.Timestamp);
            Assert.AreEqual("OPEN_HAND", state.Gesture);
            Assert.IsTrue(state.Mirrored);
            Assert.IsNull(PacketFixture.State(1, tracking: false).Position);
            var json = PacketFixture.Json(long.MaxValue); json["timestamp"] = long.MaxValue;
            byte[] bytes = PacketFixture.Bytes(json);
            Assert.IsTrue(CvStateCodec.TryDecode(bytes, bytes.Length, out CvState boundary));
            Assert.AreEqual(long.MaxValue, boundary.Sequence);
        }

        [TestCase("OPEN_HAND")][TestCase("FIST")][TestCase("PINCH")][TestCase("POINT")][TestCase("UNKNOWN")]
        public void AllProtocolGesturesAreAccepted(string gesture)
        {
            var json = PacketFixture.Json(); json["gesture"] = gesture;
            byte[] bytes = PacketFixture.Bytes(json);
            Assert.IsTrue(CvStateCodec.TryDecode(bytes, bytes.Length, out CvState state));
            Assert.AreEqual(gesture, state.Gesture);
        }

        [Test]
        public void EveryRequiredFieldMustBePresent()
        {
            foreach (var property in PacketFixture.Json().Properties())
            {
                var json = PacketFixture.Json(); json.Remove(property.Name);
                Reject(PacketFixture.Bytes(json));
            }
        }

        [TestCase("version", "true")][TestCase("version", "2")]
        [TestCase("sequence", "1.5")][TestCase("sequence", "-1")][TestCase("sequence", "true")]
        [TestCase("timestamp", "-1")][TestCase("timestamp", "9223372036854775808")]
        [TestCase("stream_id", "\"bad\"")][TestCase("stream_id", "\"A1E111A1-1111-4111-8111-111111111111\"")]
        [TestCase("tracking", "1")][TestCase("mirrored", "\"true\"")]
        [TestCase("hand", "\"both\"")][TestCase("gesture", "\"WAVE\"")]
        [TestCase("confidence", "true")][TestCase("confidence", "1.1")]
        [TestCase("confidence", "-0.1")][TestCase("confidence", "NaN")]
        [TestCase("position", "null")][TestCase("position", "[]")]
        public void InvalidRequiredValuesAreRejected(string key, string value)
        {
            var json = PacketFixture.Json(); json[key] = JToken.Parse(value);
            Reject(PacketFixture.Bytes(json));
        }

        [TestCase("x", "-0.01")][TestCase("y", "1.01")][TestCase("z", "Infinity")]
        [TestCase("x", "true")][TestCase("y", "\"0.3\"")]
        public void InvalidPositionValuesAreRejected(string key, string value)
        {
            var json = PacketFixture.Json(); json["position"][key] = JToken.Parse(value);
            Reject(PacketFixture.Bytes(json));
        }

        [Test]
        public void LostPacketsCannotCarryStaleState()
        {
            var json = PacketFixture.Json(tracking: false); json["position"] = PacketFixture.Json()["position"];
            Reject(PacketFixture.Bytes(json));
            json = PacketFixture.Json(tracking: false); json["gesture"] = "FIST";
            Reject(PacketFixture.Bytes(json));
            json = PacketFixture.Json(tracking: false); json["confidence"] = 0.1;
            Reject(PacketFixture.Bytes(json));
        }

        [Test]
        public void MalformedDuplicateOversizedAndNonUtf8PacketsAreRejected()
        {
            foreach (string value in new[] { "", "null", "[]", "{", "{\"type\":\"CV_STATE\",\"type\":\"CV_STATE\"}" })
                Reject(Encoding.UTF8.GetBytes(value));
            Reject(new byte[] { 0xff });
            Reject(new byte[CvStateCodec.MaxDatagramBytes + 1]);
            string valid = PacketFixture.Json().ToString();
            Reject(Encoding.UTF8.GetBytes(valid + valid));
            Reject(Encoding.UTF8.GetBytes(valid.Replace("\"version\": 1", "\"version\": 1, \"version\": 1")));
        }

        [Test]
        public void ExtraFieldsAreIgnoredButNonfiniteNumbersAndCommentsAreRejected()
        {
            var json = PacketFixture.Json(); json["future_metric"] = 5;
            byte[] bytes = PacketFixture.Bytes(json);
            Assert.IsTrue(CvStateCodec.TryDecode(bytes, bytes.Length, out _));
            json["future_metric"] = double.NaN;
            // Json.NET serializes NaN as a quoted string by default. Use an
            // actual invalid numeric token to exercise the wire validator.
            string nonfinite = json.ToString(Newtonsoft.Json.Formatting.None)
                .Replace("\"future_metric\":\"NaN\"", "\"future_metric\":NaN");
            Reject(Encoding.UTF8.GetBytes(nonfinite));
            string valid = PacketFixture.Json().ToString();
            Reject(Encoding.UTF8.GetBytes("/* comment */" + valid));
            Reject(Encoding.UTF8.GetBytes(valid.Replace("\"hand\"", "'hand'")));
        }

        [Test]
        public void ASlotIsOptionalAndMustBeAPlayerNumber()
        {
            Assert.IsNull(PacketFixture.State().Slot); // the original single-player packet has no slot
            foreach (int slot in new[] { 0, 1 })
            {
                var json = PacketFixture.Json(); json["slot"] = slot;
                byte[] bytes = PacketFixture.Bytes(json);
                Assert.IsTrue(CvStateCodec.TryDecode(bytes, bytes.Length, out CvState state));
                Assert.AreEqual(slot, state.Slot);
            }
            var explicitNull = PacketFixture.Json(); explicitNull["slot"] = JValue.CreateNull();
            byte[] nullBytes = PacketFixture.Bytes(explicitNull);
            Assert.IsTrue(CvStateCodec.TryDecode(nullBytes, nullBytes.Length, out CvState nullState));
            Assert.IsNull(nullState.Slot);
        }

        [TestCase("2")]
        [TestCase("-1")]
        [TestCase("1.5")]
        [TestCase("true")]
        [TestCase("\"0\"")]
        public void InvalidSlotsAreRejected(string value)
        {
            var json = PacketFixture.Json(); json["slot"] = JToken.Parse(value);
            Reject(PacketFixture.Bytes(json));
        }

        private static void Reject(byte[] bytes)
        {
            Assert.IsFalse(CvStateCodec.TryDecode(bytes, bytes.Length, out CvState state));
            Assert.IsNull(state);
        }
    }

    public sealed class CvStateBufferTests
    {
        [Test]
        public void FreshnessExpiresExactlyAtTimeoutWithoutExposingStalePosition()
        {
            var buffer = new CvStateBuffer(0.5);
            Assert.IsNull(buffer.Read(0).State);
            Assert.IsTrue(buffer.TryAccept(PacketFixture.State(), 1));
            Assert.IsTrue(buffer.Read(1.499).Tracking);
            Assert.IsNull(buffer.Read(1.5).State);
            Assert.IsFalse(buffer.Read(1.5).Tracking);
            Assert.IsTrue(buffer.TryAccept(PacketFixture.State(1), 1.6));
            Assert.IsTrue(buffer.Read(1.6).Tracking);
        }

        [Test]
        public void DuplicateAndOlderPacketsDoNotRefreshTheReceiveTimeout()
        {
            var buffer = new CvStateBuffer(0.5);
            buffer.TryAccept(PacketFixture.State(10), 0);
            Assert.IsFalse(buffer.TryAccept(PacketFixture.State(10), 0.4));
            Assert.IsFalse(buffer.TryAccept(PacketFixture.State(9), 0.45));
            Assert.IsNull(buffer.Read(0.5).State);
            Assert.AreEqual(2, buffer.Read(0.5).Rejected);
        }

        [Test]
        public void LostPacketClearsTrackingImmediatelyAndInvalidPacketsDoNotRefreshIt()
        {
            var buffer = new CvStateBuffer(0.5);
            buffer.TryAccept(PacketFixture.State(), 0);
            buffer.RecordInvalid();
            Assert.IsNull(buffer.Read(0.5).State);
            buffer.TryAccept(PacketFixture.State(1, tracking: false), 0.6);
            Assert.IsFalse(buffer.Read(0.6).Tracking);
            Assert.IsNull(buffer.Read(0.6).State.Position);
            Assert.AreEqual(1, buffer.Read(0.6).Invalid);
        }

        [Test]
        public void RestartRequiresSilenceAndRetiredStreamCanNeverReactivate()
        {
            var buffer = new CvStateBuffer(0.5);
            buffer.TryAccept(PacketFixture.State(20), 0);
            Assert.IsFalse(buffer.TryAccept(PacketFixture.State(0, PacketFixture.StreamB), 0.4));
            Assert.IsTrue(buffer.TryAccept(PacketFixture.State(1, PacketFixture.StreamB), 0.5));
            Assert.IsFalse(buffer.TryAccept(PacketFixture.State(21), 1.1));
            Assert.IsNull(buffer.Read(1.1).State);
            Assert.IsTrue(buffer.TryAccept(PacketFixture.State(2, PacketFixture.StreamB), 1.2));
        }

        [Test]
        public void StreamRetirementBudgetIsBoundedWithoutEvictingRetiredIds()
        {
            var buffer = new CvStateBuffer(0.5);
            for (int index = 0; index <= 128; index++)
                Assert.IsTrue(buffer.TryAccept(PacketFixture.State(0, Guid.NewGuid().ToString("D")), index));
            Assert.IsFalse(buffer.TryAccept(PacketFixture.State(0, Guid.NewGuid().ToString("D")), 129));
        }

        [Test]
        public void ReaderClockRaceClampsAgeAndInvalidReceiveClockIsRejected()
        {
            var buffer = new CvStateBuffer(0.5);
            buffer.TryAccept(PacketFixture.State(), 1);
            Assert.AreEqual(0, buffer.Read(0.999).AgeSeconds);
            Assert.Throws<ArgumentException>(() => buffer.TryAccept(PacketFixture.State(1), 0.9));
            Assert.Throws<ArgumentOutOfRangeException>(() => buffer.Read(double.NaN));
            Assert.Throws<ArgumentOutOfRangeException>(() => new CvStateBuffer(0));
        }
    }

    public sealed class ReceiverConfigurationTests
    {
        [Test]
        public void DefaultsFileAndEnvironmentFollowPrecedenceWithoutReadingOtherSettings()
        {
            Assert.AreEqual(5005, ReceiverConfiguration.Load(null, _ => null).Port);
            string path = Path.GetTempFileName();
            try
            {
                File.WriteAllText(path, "CV_TO_UNITY_PORT=6100\nexport UNITY_RECEIVE_TIMEOUT='0.75' # seconds\nMONGODB_URI=ignore-me\n");
                var settings = ReceiverConfiguration.Load(path, key => key == "CV_TO_UNITY_PORT" ? "6200" : null);
                Assert.AreEqual(6200, settings.Port);
                Assert.AreEqual(0.75, settings.TimeoutSeconds);
            }
            finally { File.Delete(path); }
        }

        [TestCase("CV_TO_UNITY_PORT", "1023")][TestCase("CV_TO_UNITY_PORT", "65536")]
        [TestCase("CV_TO_UNITY_PORT", "1.5")][TestCase("CV_TO_UNITY_PORT", "")]
        [TestCase("UNITY_TO_PYTHON_PORT", "5005")][TestCase("UNITY_RECEIVE_TIMEOUT", "0")]
        [TestCase("UNITY_RECEIVE_TIMEOUT", "NaN")][TestCase("UNITY_RECEIVE_TIMEOUT", "Infinity")]
        public void InvalidSettingsIdentifyTheirKey(string key, string value)
        {
            var error = Assert.Throws<ArgumentException>(() => ReceiverConfiguration.Load(null, name => name == key ? value : null));
            StringAssert.Contains(key, error.Message);
        }
    }

    public sealed class UdpStateListenerTests
    {
        [Test]
        public void RealSocketReceivesRejectsMalformedTimesOutAndRebindsAfterStop()
        {
            int port = FreePort();
            using (var listener = new UdpStateListener(new ReceiverConfiguration(port, 0.1)))
            using (var sender = new UdpClient())
            {
                listener.Start();
                listener.Start(); // Same listener does not open a second socket.
                byte[] invalid = Encoding.UTF8.GetBytes("invalid");
                sender.Send(invalid, invalid.Length, "127.0.0.1", port);
                Send(sender, port, PacketFixture.Json());
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read().Accepted == 1, 2000));
                Assert.IsTrue(listener.Read().Tracking);
                Assert.AreEqual(1, listener.Read().Invalid);
                Send(sender, port, PacketFixture.Json());
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read().Rejected == 1, 2000));
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read().State == null, 2000));
                listener.Stop();
                listener.Stop();
                Assert.IsFalse(listener.IsRunning);
                Assert.IsNull(listener.Read().State);
                Assert.IsNull(listener.LastError);
                listener.Start();
                Send(sender, port, PacketFixture.Json(0, PacketFixture.StreamB));
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read().Accepted == 1, 2000));
                Assert.AreEqual(PacketFixture.StreamB, listener.Read().State.StreamId);
            }
        }

        [Test]
        public void EachPlayerHasTheirOwnStreamAndSlotlessPacketsGoToPlayerZero()
        {
            int port = FreePort();
            using (var listener = new UdpStateListener(new ReceiverConfiguration(port, 5)))
            using (var sender = new UdpClient())
            {
                listener.Start();
                var first = PacketFixture.Json(0, PacketFixture.StreamA); first["slot"] = 0;
                var second = PacketFixture.Json(0, PacketFixture.StreamB); second["slot"] = 1;
                second["position"]["x"] = 0.8;
                Send(sender, port, first);
                Send(sender, port, second); // a different stream, but a different player: not refused
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read(0).Accepted == 1 && listener.Read(1).Accepted == 1, 2000));
                Assert.AreEqual(PacketFixture.StreamA, listener.Read(0).State.StreamId);
                Assert.AreEqual(PacketFixture.StreamB, listener.Read(1).State.StreamId);
                Assert.AreEqual(0.55, listener.Read(0).State.Position.X);
                Assert.AreEqual(0.8, listener.Read(1).State.Position.X);
                Assert.AreEqual(0, listener.Read(0).Rejected + listener.Read(1).Rejected);
                Assert.AreEqual(0, listener.Read().State.Slot); // Read() is player 0

                // Player 1 losing tracking does not touch player 0.
                var lost = PacketFixture.Json(1, PacketFixture.StreamB, tracking: false); lost["slot"] = 1;
                Send(sender, port, lost);
                Assert.IsTrue(SpinWait.SpinUntil(() => !listener.Read(1).Tracking, 2000));
                Assert.IsTrue(listener.Read(0).Tracking);

                // The original single-player packet (no slot) is player 0's.
                Send(sender, port, PacketFixture.Json(1, PacketFixture.StreamA));
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read(0).Accepted == 2, 2000));
                Assert.IsNull(listener.Read(0).State.Slot);
                Assert.AreEqual(2, listener.Read(0).Accepted);
                Assert.AreEqual(2, listener.Read(1).Accepted);
                Assert.Throws<ArgumentOutOfRangeException>(() => listener.Read(2));
                Assert.Throws<ArgumentOutOfRangeException>(() => listener.Read(-1));
            }
        }

        [Test]
        public void MalformedPacketsAreCountedForBothPlayers()
        {
            int port = FreePort();
            using (var listener = new UdpStateListener(new ReceiverConfiguration(port, 5)))
            using (var sender = new UdpClient())
            {
                listener.Start();
                byte[] invalid = Encoding.UTF8.GetBytes("invalid");
                sender.Send(invalid, invalid.Length, "127.0.0.1", port);
                Assert.IsTrue(SpinWait.SpinUntil(() => listener.Read(0).Invalid == 1 && listener.Read(1).Invalid == 1, 2000));
            }
        }

        [Test]
        public void OccupiedPortFailsWithoutLeavingAWorkerBehind()
        {
            int port = FreePort();
            using (var first = new UdpStateListener(new ReceiverConfiguration(port)))
            using (var second = new UdpStateListener(new ReceiverConfiguration(port)))
            {
                first.Start();
                Assert.Throws<SocketException>(() => second.Start());
                Assert.IsFalse(second.IsRunning);
                first.Stop();
                second.Start();
                Assert.IsTrue(second.IsRunning);
            }
        }

        private static void Send(UdpClient sender, int port, JObject json)
        {
            byte[] bytes = PacketFixture.Bytes(json);
            sender.Send(bytes, bytes.Length, "127.0.0.1", port);
        }

        private static int FreePort()
        {
            using (var socket = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0)))
                return ((IPEndPoint)socket.Client.LocalEndPoint).Port;
        }
    }
}
