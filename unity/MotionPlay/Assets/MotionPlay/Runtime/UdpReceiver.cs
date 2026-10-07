using System;
using System.IO;
using MotionPlay.Networking;
using UnityEngine;

namespace MotionPlay.Unity
{
    /// <summary>Unity lifecycle adapter. All Unity API calls occur on the main thread.</summary>
    [DisallowMultipleComponent]
    [DefaultExecutionOrder(-100)]
    public sealed class UdpReceiver : MonoBehaviour
    {
        [SerializeField, Tooltip("Optional external .env path; never bundle database credentials in the Unity build.")]
        private string envFileOverride = "";
        [SerializeField] private string status = "Stopped";
        private UdpStateListener listener;
        private bool wasTracking;
        private string appliedStream;
        private long appliedSequence = -1;
        private double lastSummary;

        private readonly ReceiverSnapshot[] snapshots = new ReceiverSnapshot[CvState.MaxSlots];

        /// <summary>Player 0, which is the only player in single-player games.</summary>
        public ReceiverSnapshot Snapshot => snapshots[0];
        public CvState CurrentState => Snapshot?.State;

        /// <summary>The latest state of one player (0 or 1); null before the first read or while stopped.</summary>
        public ReceiverSnapshot GetSnapshot(int slot)
        {
            if (slot < 0 || slot >= snapshots.Length) throw new ArgumentOutOfRangeException(nameof(slot));
            return snapshots[slot];
        }
        public string Status => status;
        public double LastApplyDelayMs { get; private set; }

        private void OnEnable()
        {
            if (!Application.isPlaying) return;
            Application.runInBackground = true;
            try
            {
                string path = ResolveEnvFile();
                var options = ReceiverConfiguration.Load(path, Environment.GetEnvironmentVariable);
                listener = new UdpStateListener(options);
                listener.Start();
                status = "Listening on 127.0.0.1:" + options.Port;
                lastSummary = UdpStateListener.MonotonicSeconds;
                Debug.Log("MotionPlay Unity receiver started. " + status +
                          "; receive timeout " + options.TimeoutSeconds + "s.", this);
            }
            catch (Exception error) when (error is ArgumentException || error is IOException ||
                error is UnauthorizedAccessException || error is System.Net.Sockets.SocketException)
            {
                listener?.Dispose();
                listener = null;
                status = "Receiver unavailable: check .env, port ownership, and socket permissions.";
                // Config validation errors contain setting names, never credential values.
                Debug.LogError("MotionPlay: " + status + " " + error.Message, this);
            }
        }

        private string ResolveEnvFile() => ResolveEnvFile(envFileOverride);

        /// <summary>Locate the .env file shared by the receiver and the result sender.</summary>
        internal static string ResolveEnvFile(string envFileOverride)
        {
            string path = string.IsNullOrWhiteSpace(envFileOverride)
                ? Environment.GetEnvironmentVariable("MOTIONPLAY_ENV_FILE") : envFileOverride;
            if (!string.IsNullOrWhiteSpace(path))
            {
                if (!File.Exists(path)) throw new IOException("The specified external .env file does not exist.");
                return path;
            }
#if UNITY_EDITOR
            // Assets is repo/unity/MotionPlay/Assets; the repository .env is three levels above.
            return Path.GetFullPath(Path.Combine(Application.dataPath, "../../..", ".env"));
#else
            // A player build uses defaults/process variables unless explicitly given an external file.
            return null;
#endif
        }

        private void Update()
        {
            if (listener == null) return;
            for (int slot = 0; slot < snapshots.Length; slot++) snapshots[slot] = listener.Read(slot);
            if (!listener.IsRunning)
            {
                string error = listener.LastError ?? "UDP listener stopped.";
                if (status != error) Debug.LogError("MotionPlay: " + error, this);
                status = error;
                return;
            }
            if (Snapshot.State != null)
            {
                if (Snapshot.State.StreamId != appliedStream)
                    Debug.Log("MotionPlay adopted CV stream " + Snapshot.State.StreamId + ".", this);
                if (Snapshot.State.StreamId != appliedStream || Snapshot.State.Sequence != appliedSequence)
                    LastApplyDelayMs = Snapshot.AgeSeconds.GetValueOrDefault() * 1000;
                appliedStream = Snapshot.State.StreamId;
                appliedSequence = Snapshot.State.Sequence;
            }
            if (Snapshot.Tracking != wasTracking)
                Debug.Log(Snapshot.Tracking ? "MotionPlay receiver tracking restored." :
                          "MotionPlay receiver tracking cleared (lost packet or receive timeout).", this);
            wasTracking = Snapshot.Tracking;
            double now = UdpStateListener.MonotonicSeconds;
            if (now - lastSummary >= 5)
            {
                Debug.Log("MotionPlay UDP totals: " + Snapshot.Accepted + " accepted, " +
                          Snapshot.Invalid + " invalid, " + Snapshot.Rejected + " rejected by ordering/stream policy.", this);
                lastSummary = now;
            }
        }

        private void StopReceiver()
        {
            if (listener != null)
            {
                listener.Dispose();
                if (listener.LastError != null) Debug.LogError("MotionPlay: " + listener.LastError, this);
                listener = null;
                Debug.Log("MotionPlay Unity receiver stopped; socket released.", this);
            }
            for (int slot = 0; slot < snapshots.Length; slot++) snapshots[slot] = null;
            wasTracking = false;
            appliedStream = null;
            appliedSequence = -1;
            LastApplyDelayMs = 0;
            status = "Stopped";
        }

        private void OnDisable() => StopReceiver();
        private void OnDestroy() => StopReceiver();
        private void OnApplicationQuit() => StopReceiver();
    }
}
