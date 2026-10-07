using System;
using System.Linq;
using System.Text;
using MotionPlay.Games;
using MotionPlay.Networking;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MotionPlay.Tests
{
    public sealed class QuizSessionResultTests
    {
        private const string Round = "d4e444d4-4444-4444-8444-444444444444";
        private const string Session = "e5e555e5-5555-4555-8555-555555555555";

        /// <summary>A real round played through the game: player 1 right every time, player 2 right on every other question.</summary>
        private static ScamQuizGame PlayedRound()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.PlayRound(game, q => true, q => q % 2 == 0, think: 2);
            return game;
        }

        private static QuizSessionResult FromGame(ScamQuizGame game, int slot) =>
            QuizSessionResult.FromStats(game.Stats(slot), slot, slot == 0 ? "left" : "right", "level_1", Session, Round, 1,
                1770000000000, 1770000090000);

        [Test]
        public void EncodesExactlyTheAgreedFieldSet()
        {
            ScamQuizGame game = PlayedRound();
            QuizSessionResult result = FromGame(game, 0);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            JObject json = JObject.Parse(Encoding.UTF8.GetString(QuizSessionResultCodec.Encode(result)));
            string[] expected =
            {
                "type", "version", "stream_id", "sequence", "timestamp", "session_id", "game", "hand", "difficulty",
                "startedAt", "endedAt", "duration", "roundId", "slot", "bankVersion", "totalQuestions", "correct",
                "wrong", "skipped", "bestStreak", "averageResponseTime", "fastestResponse", "slowestResponse", "responses"
            };
            CollectionAssert.AreEquivalent(expected, json.Properties().Select(p => p.Name).ToArray());
            Assert.AreEqual("QUIZ_SESSION_END", (string)json["type"]);
            Assert.AreEqual("scam_quiz", (string)json["game"]);
            Assert.AreEqual(JTokenType.Integer, json["slot"].Type);
            Assert.AreEqual(JTokenType.Integer, json["sequence"].Type);
            Assert.AreEqual(90.0, (double)json["duration"], 1e-9);
        }

        [Test]
        public void ResponsesAreIdNumberAndWholeMillisecondsOrNull()
        {
            ScamQuizGame game = PlayedRound();
            QuizSessionResult result = FromGame(game, 1);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            JArray responses = (JArray)JObject.Parse(Encoding.UTF8.GetString(QuizSessionResultCodec.Encode(result)))["responses"];
            Assert.AreEqual(5, responses.Count);
            for (int i = 0; i < 5; i++)
            {
                var row = (JArray)responses[i];
                Assert.AreEqual(3, row.Count);
                Assert.AreEqual(game.Stats(1).Responses[i].QuestionId, (string)row[0]);
                Assert.AreEqual(JTokenType.Integer, row[1].Type);
                Assert.AreEqual(JTokenType.Integer, row[2].Type, "milliseconds are whole numbers, not fractions");
            }
        }

        [Test]
        public void ATimedOutQuestionHasNoAnswerAndNoTime()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.Idle(game, game.QuestionSeconds + 1);
            QuizDriver.FinishFeedback(game);
            QuizDriver.ChooseBoth(game, true, true);
            QuizSessionResult result = FromGame(game, 0);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            JArray responses = (JArray)JObject.Parse(Encoding.UTF8.GetString(QuizSessionResultCodec.Encode(result)))["responses"];
            Assert.AreEqual(-1, (int)((JArray)responses[0])[1]);
            Assert.AreEqual(JTokenType.Null, ((JArray)responses[0])[2].Type);
            Assert.AreEqual(1, result.Skipped);
        }

        [Test]
        public void AllTheStatisticsComeFromThePlayersRound()
        {
            ScamQuizGame game = PlayedRound();
            foreach (int slot in new[] { 0, 1 })
            {
                ScamQuizStats stats = game.Stats(slot);
                QuizSessionResult result = FromGame(game, slot);
                Assert.AreEqual(slot, result.Slot);
                Assert.AreEqual((stats.Total, stats.Correct, stats.Wrong, stats.Skipped, stats.BestStreak),
                    (result.TotalQuestions, result.Correct, result.Wrong, result.Skipped, result.BestStreak));
                Assert.AreEqual(stats.AverageResponseTime, result.AverageResponseTime);
                Assert.AreEqual(stats.FastestResponse, result.FastestResponse);
                Assert.AreEqual(stats.SlowestResponse, result.SlowestResponse);
                CollectionAssert.AreEqual(stats.Responses.Select(r => r.QuestionId).ToArray(), result.Responses.Select(r => r.QuestionId).ToArray());
            }
            Assert.AreEqual(5, game.Stats(0).Correct);
            Assert.AreEqual(3, game.Stats(1).Correct); // questions 0, 2 and 4
        }

        [Test]
        public void ARoundWithNoAnswersHasNullTimesNotZero()
        {
            ScamQuizGame game = QuizDriver.Started();
            for (int q = 0; q < 5; q++) { QuizDriver.Idle(game, game.QuestionSeconds + 1); QuizDriver.FinishFeedback(game); }
            QuizSessionResult result = FromGame(game, 0);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            JObject json = JObject.Parse(Encoding.UTF8.GetString(QuizSessionResultCodec.Encode(result)));
            Assert.AreEqual(JTokenType.Null, json["averageResponseTime"].Type);
            Assert.AreEqual(JTokenType.Null, json["fastestResponse"].Type);
            Assert.AreEqual(JTokenType.Null, json["slowestResponse"].Type);
            Assert.AreEqual(5, (int)json["skipped"]);
        }

        [Test]
        public void ATwentyQuestionRoundWithTypicalIdsFitsInOneDatagram()
        {
            ScamQuizGame game = QuizDriver.NewGame(level: 5, bank: QuizFixture.Bank(perDifficulty: 6));
            ScamQuizSettings settings = ScamQuizDifficulty.SettingsFor(5, roundLength: 20);
            game.ApplySettings(settings);
            game.Step(QuizDriver.Frame, QuizDriver.Hands(true, true));
            QuizDriver.PlayRound(game, q => true, q => true);
            Assert.AreEqual(20, game.Stats(0).Total);
            QuizSessionResult result = FromGame(game, 0);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            Assert.LessOrEqual(QuizSessionResultCodec.Encode(result).Length, SessionResultCodec.MaxDatagramBytes);
        }

        [Test]
        public void AnOversizedMessageIsRefusedRatherThanSentTruncated()
        {
            QuizSessionResult result = FromGame(PlayedRound(), 0);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            result.RoundId = new string('r', SessionResultCodec.MaxDatagramBytes);
            Assert.Throws<InvalidOperationException>(() => QuizSessionResultCodec.Encode(result));
        }

        [Test]
        public void NonFiniteNumbersAndMissingInputsAreRefused()
        {
            QuizSessionResult result = FromGame(PlayedRound(), 0);
            result.StreamId = "b2e222b2-2222-4222-8222-222222222222";
            result.AverageResponseTime = double.NaN;
            Assert.Throws<ArgumentException>(() => QuizSessionResultCodec.Encode(result));
            result.AverageResponseTime = 1;
            result.Duration = double.PositiveInfinity;
            Assert.Throws<ArgumentException>(() => QuizSessionResultCodec.Encode(result));
            Assert.Throws<ArgumentNullException>(() => QuizSessionResultCodec.Encode(null));
            Assert.Throws<ArgumentNullException>(() => QuizSessionResult.FromStats(null, 0, "left", "level_1", Session, Round, 1, 0, 1));
        }

        [Test]
        public void TheEndTimeNeverPrecedesTheStartTime()
        {
            QuizSessionResult result = QuizSessionResult.FromStats(PlayedRound().Stats(0), 0, "left", "level_1", Session, Round, 1,
                1770000050000, 1770000000000);
            Assert.AreEqual(result.StartedAt, result.EndedAt);
            Assert.AreEqual(0.0, result.Duration);
        }
    }
}
