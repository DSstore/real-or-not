namespace MotionPlay.Networking
{
    /// <summary>Immutable normalized palm position; Z is model depth, not meters.</summary>
    public sealed class PalmPosition
    {
        public double X { get; }
        public double Y { get; }
        public double Z { get; }

        internal PalmPosition(double x, double y, double z) { X = x; Y = y; Z = z; }
    }

    /// <summary>A validated CV_STATE packet independent of Unity or gameplay.</summary>
    public sealed class CvState
    {
        /// <summary>Players that can share one camera; a packet's slot is 0 to MaxSlots - 1.</summary>
        public const int MaxSlots = 2;

        public string StreamId { get; }
        public long Sequence { get; }
        public long Timestamp { get; }
        public string Hand { get; }
        public bool Tracking { get; }
        public PalmPosition Position { get; }
        public string Gesture { get; }
        public double Confidence { get; }
        public bool Mirrored { get; }
        /// <summary>
        /// Which player this stream carries in two-player mode (0 or 1); null for the original single-player stream,
        /// which the receiver treats as player 0. In two-player mode <see cref="Hand"/> only names the player's zone.
        /// </summary>
        public int? Slot { get; }

        internal CvState(string streamId, long sequence, long timestamp, string hand,
            bool tracking, PalmPosition position, string gesture, double confidence, bool mirrored, int? slot = null)
        {
            StreamId = streamId; Sequence = sequence; Timestamp = timestamp; Hand = hand;
            Tracking = tracking; Position = position; Gesture = gesture;
            Confidence = confidence; Mirrored = mirrored; Slot = slot;
        }
    }
}
