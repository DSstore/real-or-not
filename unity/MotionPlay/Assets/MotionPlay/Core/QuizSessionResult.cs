using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MotionPlay.Games;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MotionPlay.Networking
{
    /// <summary>
    /// One player's finished scam quiz round, in the shape of the Python QUIZ_SESSION_END message
    /// (docs/udp_protocol.md). Each player of a round sends their own, with a different session id and the same
    /// round id. The category, difficulty and right answer of each question are not sent; the receiver looks them up.
    /// </summary>
    public sealed class QuizSessionResult
    {
        public const string GameName = "scam_quiz";

        public string StreamId { get; set; }
        public long Sequence { get; set; }
        public long Timestamp { get; set; }
        public string SessionId { get; set; }
        public string Game { get; set; } = GameName;
        /// <summary>Left or right. In a two-player game this is the player's zone, not necessarily the hand they used.</summary>
        public string Hand { get; set; }
        /// <summary>The level the round was played at, e.g. <c>level_2</c>.</summary>
        public string Difficulty { get; set; }
        public long StartedAt { get; set; }
        public long EndedAt { get; set; }
        public double Duration { get; set; }
        /// <summary>Shared by the players of one round.</summary>
        public string RoundId { get; set; }
        /// <summary>The player: 0 or 1.</summary>
        public int Slot { get; set; }
        public int BankVersion { get; set; }
        public int TotalQuestions { get; set; }
        public int Correct { get; set; }
        public int Wrong { get; set; }
        public int Skipped { get; set; }
        public int BestStreak { get; set; }
        public double? AverageResponseTime { get; set; }
        public double? FastestResponse { get; set; }
        public double? SlowestResponse { get; set; }
        public IReadOnlyList<QuizResponse> Responses { get; set; } = new QuizResponse[0];

        /// <summary>Copy a player's round statistics. Envelope fields (stream, sequence, time) come from the sender.</summary>
        public static QuizSessionResult FromStats(ScamQuizStats stats, int slot, string hand, string difficulty,
            string sessionId, string roundId, int bankVersion, long startedAtMs, long endedAtMs)
        {
            if (stats == null) throw new ArgumentNullException(nameof(stats));
            return new QuizSessionResult
            {
                SessionId = sessionId, Hand = hand, Difficulty = difficulty, RoundId = roundId, Slot = slot,
                BankVersion = bankVersion, StartedAt = startedAtMs, EndedAt = Math.Max(startedAtMs, endedAtMs),
                Duration = Math.Max(0, endedAtMs - startedAtMs) / 1000.0,
                TotalQuestions = stats.Total, Correct = stats.Correct, Wrong = stats.Wrong, Skipped = stats.Skipped,
                BestStreak = stats.BestStreak, AverageResponseTime = stats.AverageResponseTime,
                FastestResponse = stats.FastestResponse, SlowestResponse = stats.SlowestResponse,
                Responses = stats.Responses.ToList().AsReadOnly()
            };
        }
    }

    /// <summary>Encode QUIZ_SESSION_END; no engine types.</summary>
    public static class QuizSessionResultCodec
    {
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

        public static byte[] Encode(QuizSessionResult result)
        {
            if (result == null) throw new ArgumentNullException(nameof(result));
            var responses = new JArray();
            foreach (QuizResponse response in result.Responses)
                responses.Add(new JArray(response.QuestionId, response.Selected,
                    response.ResponseMs.HasValue ? (JToken)response.ResponseMs.Value : JValue.CreateNull()));
            var json = new JObject
            {
                ["type"] = "QUIZ_SESSION_END", ["version"] = 1,
                ["stream_id"] = result.StreamId, ["sequence"] = result.Sequence, ["timestamp"] = result.Timestamp,
                ["session_id"] = result.SessionId, ["game"] = result.Game, ["hand"] = result.Hand,
                ["difficulty"] = result.Difficulty, ["startedAt"] = result.StartedAt, ["endedAt"] = result.EndedAt,
                ["duration"] = Finite(result.Duration),
                ["roundId"] = result.RoundId, ["slot"] = result.Slot, ["bankVersion"] = result.BankVersion,
                ["totalQuestions"] = result.TotalQuestions, ["correct"] = result.Correct, ["wrong"] = result.Wrong,
                ["skipped"] = result.Skipped, ["bestStreak"] = result.BestStreak,
                ["averageResponseTime"] = Nullable(result.AverageResponseTime),
                ["fastestResponse"] = Nullable(result.FastestResponse),
                ["slowestResponse"] = Nullable(result.SlowestResponse),
                ["responses"] = responses
            };
            byte[] bytes = StrictUtf8.GetBytes(json.ToString(Formatting.None));
            if (bytes.Length > SessionResultCodec.MaxDatagramBytes)
                throw new InvalidOperationException("QUIZ_SESSION_END is too large.");
            return bytes;
        }

        private static double Finite(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value)) throw new ArgumentException("Metric must be finite.");
            return value;
        }

        private static JToken Nullable(double? value) => value.HasValue ? new JValue(Finite(value.Value)) : JValue.CreateNull();
    }
}
