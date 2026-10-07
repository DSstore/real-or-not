using System;
using System.Linq;
using MotionPlay.Games;
using NUnit.Framework;

namespace MotionPlay.Tests
{
    public sealed class ScamQuizDifficultyTests
    {
        /// <summary>Play a whole round at the adapter's current level and return the players' statistics.</summary>
        private static ScamQuizStats[] Round(ScamQuizDifficulty difficulty, Func<int, bool> first, Func<int, bool> second,
            double think = 0)
        {
            ScamQuizGame game = QuizDriver.Started(difficulty.Level);
            QuizDriver.PlayRound(game, first, second, think);
            return new[] { game.Stats(0), game.Stats(1) };
        }

        private static readonly Func<int, bool> Right = q => true;
        private static readonly Func<int, bool> Wrong = q => false;

        [Test]
        public void StartsAtTheEasiestLevelAndKnowsItsLabel()
        {
            var difficulty = new ScamQuizDifficulty();
            Assert.AreEqual(1, difficulty.Level);
            Assert.AreEqual("level_1", difficulty.Label);
            Assert.AreEqual("level_5", ScamQuizDifficulty.LabelFor(99));
            Assert.AreEqual("level_1", ScamQuizDifficulty.LabelFor(-3));
            Assert.AreEqual(4, ScamQuizDifficulty.ParseLabel("level_4"));
            foreach (string bad in new[] { null, "", "default", "level_", "level_0", "level_6", "level_-1", "level_2x", "Level_2" })
                Assert.IsNull(ScamQuizDifficulty.ParseLabel(bad), bad);
        }

        [Test]
        public void EachLevelIsHarderThanTheLastAndShowsMoreAnswers()
        {
            var presets = Enumerable.Range(1, 5).Select(level => ScamQuizDifficulty.SettingsFor(level)).ToArray();
            CollectionAssert.AreEqual(new[] { 25.0, 20, 15, 12, 10 }, presets.Select(p => p.QuestionSeconds).ToArray());
            CollectionAssert.AreEqual(new[] { 2, 2, 3, 4, 4 }, presets.Select(p => p.ChoiceCount).ToArray());
            CollectionAssert.AreEqual(new[] { 1.2, 1.0, 0.8, 0.7, 0.6 }, presets.Select(p => p.DwellSeconds).ToArray());
            CollectionAssert.AreEqual(new[] { 1, 2, 3, 4, 5 }, presets.Select(p => p.MaxQuestionDifficulty).ToArray());
            CollectionAssert.AreEqual(new[] { 1, 2, 3, 4, 5 }, presets.Select(p => p.Level).ToArray());
            Assert.AreEqual(5, ScamQuizDifficulty.SettingsFor(1).RoundLength);
        }

        [Test]
        public void PlayersRoundLengthAndExplanationTimeArePassedThrough()
        {
            ScamQuizSettings settings = ScamQuizDifficulty.SettingsFor(2, players: 1, roundLength: 7, explanationSeconds: 3);
            Assert.AreEqual((1, 7, 3.0), (settings.Players, settings.RoundLength, settings.ExplanationSeconds));
        }

        [Test]
        public void TwoStrongQuickRoundsInARowRaiseTheLevelByOne()
        {
            var difficulty = new ScamQuizDifficulty(1);
            Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Right, Right)));
            Assert.AreEqual(1, difficulty.Level);
            Assert.AreEqual(DifficultyChange.Harder, difficulty.RecordRound(Round(difficulty, Right, Right)));
            Assert.AreEqual(2, difficulty.Level);
            // The count starts again after a change.
            Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Right, Right)));
            Assert.AreEqual(2, difficulty.Level);
        }

        [Test]
        public void ARoundInBetweenThatIsNeitherEasyNorHardResetsTheCount()
        {
            var difficulty = new ScamQuizDifficulty(2);
            difficulty.RecordRound(Round(difficulty, Right, Right));
            difficulty.RecordRound(Round(difficulty, q => q < 3, q => q < 3)); // 60%
            Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Right, Right)));
            Assert.AreEqual(2, difficulty.Level);
        }

        [Test]
        public void ASlowButPerfectRoundIsNotTooEasy()
        {
            var difficulty = new ScamQuizDifficulty(1); // 25 s allowed; slow means an average above 12.5 s
            for (int i = 0; i < 3; i++)
                Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Right, Right, think: 14)));
            Assert.AreEqual(1, difficulty.Level);
        }

        [Test]
        public void TwoWeakRoundsInARowLowerTheLevelByOne()
        {
            var difficulty = new ScamQuizDifficulty(3);
            Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Wrong, Wrong)));
            Assert.AreEqual(DifficultyChange.Easier, difficulty.RecordRound(Round(difficulty, Wrong, Wrong)));
            Assert.AreEqual(2, difficulty.Level);
        }

        [Test]
        public void TheLevelStaysWithinOneAndFive()
        {
            var lowest = new ScamQuizDifficulty(1);
            for (int i = 0; i < 4; i++) lowest.RecordRound(Round(lowest, Wrong, Wrong));
            Assert.AreEqual(1, lowest.Level);
            var highest = new ScamQuizDifficulty(5);
            for (int i = 0; i < 4; i++) highest.RecordRound(Round(highest, Right, Right));
            Assert.AreEqual(5, highest.Level);
        }

        [Test]
        public void ThePairIsJudgedTogetherNotEachPlayerAlone()
        {
            var difficulty = new ScamQuizDifficulty(2);
            for (int i = 0; i < 4; i++)
                Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Right, Wrong))); // 50% together
            Assert.AreEqual(2, difficulty.Level);
        }

        [Test]
        public void RoundsWithTooFewAnswersAreIgnored()
        {
            var difficulty = new ScamQuizDifficulty(3);
            for (int i = 0; i < 4; i++)
            {
                ScamQuizGame game = QuizDriver.Started(difficulty.Level);
                QuizDriver.Idle(game, 1); // nobody ever answers: every question is skipped
                for (int q = 0; q < 5; q++)
                {
                    QuizDriver.Idle(game, game.QuestionSeconds + 1);
                    QuizDriver.FinishFeedback(game);
                }
                Assert.AreEqual(QuizPhase.Complete, game.Phase);
                Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(game.ActiveStats()));
            }
            Assert.AreEqual(3, difficulty.Level);
        }

        [Test]
        public void OnlyPlayersWhoTookPartAreJudged()
        {
            ScamQuizGame game = QuizDriver.NewGame(level: 1);
            game.Step(QuizDriver.Frame, QuizDriver.Hands(true, false));
            Assert.IsTrue(game.StartNow());
            QuizDriver.PlayRound(game, Right, Right);
            Assert.AreEqual(1, game.ActiveStats().Count());
            var difficulty = new ScamQuizDifficulty(1);
            difficulty.RecordRound(game.ActiveStats());
            Assert.AreEqual(DifficultyChange.Harder, difficulty.RecordRound(game.ActiveStats()));
        }

        [Test]
        public void SettingTheLevelByHandClampsAndForgetsAPartialStreak()
        {
            var difficulty = new ScamQuizDifficulty(1);
            difficulty.RecordRound(Round(difficulty, Right, Right));
            difficulty.SetLevel(9);
            Assert.AreEqual(5, difficulty.Level);
            difficulty.SetLevel(2);
            Assert.AreEqual(DifficultyChange.None, difficulty.RecordRound(Round(difficulty, Right, Right)), "the earlier easy round was forgotten");
            difficulty.SetLevel(-4);
            Assert.AreEqual(1, difficulty.Level);
        }

        [Test]
        public void ANullListOfPlayersIsRefused()
        {
            Assert.Throws<ArgumentNullException>(() => new ScamQuizDifficulty().RecordRound(null));
        }
    }
}
