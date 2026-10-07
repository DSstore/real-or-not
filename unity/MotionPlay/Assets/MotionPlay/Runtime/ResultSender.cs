using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using MotionPlay.Networking;
using UnityEngine;

namespace MotionPlay.Unity
{
    /// <summary>
    /// Sends finished-round results to the Python result receiver and retries until it acknowledges them.
    /// Uses a nonblocking loopback socket polled from Update, so no thread is needed. UDP gives no
    /// delivery guarantee on its own; <see cref="State"/> reports whether the receiver confirmed storage.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class ResultSender : MonoBehaviour
    {
        [SerializeField, Tooltip("Optional external .env path; same lookup as the UDP receiver.")]
        private string envFileOverride = "";
        [SerializeField, Min(1)] private int maxAttempts = 5;
        [SerializeField, Min(0.1f)] private float retrySeconds = 1f;

        private ResultDelivery delivery;
        private UdpClient client;
        private IPEndPoint target;
        private readonly Guid streamId = Guid.NewGuid();
        private long sequence;
        private string error;

        public DeliveryState State => delivery != null ? delivery.State : DeliveryState.Idle;
        public int Attempts => delivery != null ? delivery.CurrentAttempts : 0;
        /// <summary>Results still waiting to be sent or acknowledged.</summary>
        public int Pending => delivery != null ? delivery.Pending : 0;
        /// <summary>Why results cannot be sent, or null when the sender is ready.</summary>
        public string Error => error;

        /// <summary>Stamp the envelope and queue the result; it is sent on the next Update.</summary>
        public void Submit(SessionResult result)
        {
            if (delivery == null || result == null) return;
            result.StreamId = streamId.ToString("D");
            result.Sequence = sequence++;
            result.Timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            try
            {
                delivery.Submit(result.SessionId, SessionResultCodec.Encode(result), Time.realtimeSinceStartupAsDouble);
            }
            catch (Exception problem) when (problem is ArgumentException || problem is InvalidOperationException)
            {
                error = "Result could not be encoded: " + problem.Message;
                Debug.LogError("MotionPlay: " + error, this);
            }
        }

        /// <summary>Stamp the envelope and queue one player's quiz result; it is sent, in order, on a later Update.</summary>
        public void Submit(QuizSessionResult result)
        {
            if (delivery == null || result == null) return;
            result.StreamId = streamId.ToString("D");
            result.Sequence = sequence++;
            result.Timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            try
            {
                delivery.Submit(result.SessionId, QuizSessionResultCodec.Encode(result), Time.realtimeSinceStartupAsDouble);
            }
            catch (Exception problem) when (problem is ArgumentException || problem is InvalidOperationException)
            {
                error = "Result could not be encoded: " + problem.Message;
                Debug.LogError("MotionPlay: " + error, this);
            }
        }

        private void OnEnable()
        {
            if (!Application.isPlaying) return;
            try
            {
                var options = ReceiverConfiguration.Load(UdpReceiver.ResolveEnvFile(envFileOverride),
                    Environment.GetEnvironmentVariable);
                target = new IPEndPoint(IPAddress.Loopback, options.ResultPort);
                client = new UdpClient(AddressFamily.InterNetwork);
                client.Client.Blocking = false;
                // Windows reports an ICMP "port unreachable" as a receive error when no receiver is running.
                // Disable it so a missing receiver just means unacknowledged retries.
                try { client.Client.IOControl((IOControlCode)(-1744830452), new byte[] { 0 }, null); }
                catch (Exception ignored) when (ignored is SocketException || ignored is PlatformNotSupportedException) { }
                delivery = new ResultDelivery(maxAttempts, retrySeconds);
                error = null;
            }
            catch (Exception problem) when (problem is ArgumentException || problem is IOException ||
                problem is UnauthorizedAccessException || problem is SocketException)
            {
                Close();
                error = "Result sender unavailable: check .env and socket permissions.";
                Debug.LogError("MotionPlay: " + error + " " + problem.Message, this);
            }
        }

        private void Update()
        {
            if (client == null || delivery == null) return;
            try
            {
                while (client.Available > 0)
                {
                    IPEndPoint source = null;
                    byte[] reply = client.Receive(ref source);
                    if (SessionResultCodec.TryDecodeAck(reply, reply.Length, out string id, out AckStatus status))
                    {
                        DeliveryState before = delivery.State;
                        delivery.OnAck(id, status);
                        if (delivery.State != before)
                            Debug.Log("MotionPlay result " + id + " acknowledged: " + status + ".", this);
                    }
                }
                DeliveryState previous = delivery.State;
                byte[] payload = delivery.Poll(Time.realtimeSinceStartupAsDouble);
                if (payload != null) client.Send(payload, payload.Length, target);
                if (previous == DeliveryState.Sending && delivery.State == DeliveryState.NotConfirmed)
                    Debug.LogWarning("MotionPlay: the result receiver did not acknowledge the round. " +
                        "Start it with: .\\.venv\\Scripts\\python.exe -m backend.result_receiver", this);
            }
            catch (SocketException problem)
            {
                // A transient socket error costs one attempt, which the retry schedule already absorbs.
                Debug.LogWarning("MotionPlay result socket error: " + problem.SocketErrorCode, this);
            }
        }

        private void Close()
        {
            client?.Close();
            client = null;
            delivery = null;
        }

        private void OnDisable() => Close();
        private void OnDestroy() => Close();
    }
}
