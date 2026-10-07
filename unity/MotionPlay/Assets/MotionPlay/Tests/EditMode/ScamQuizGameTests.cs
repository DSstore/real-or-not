using System;
using System.Collections.Generic;
using System.Linq;
using MotionPlay.Games;
using NUnit.Framework;

namespace MotionPlay.Tests
{
    /// <summary>Plays the quiz the way the screen layer does: one input per player each frame.</summary>
    public static class QuizDriver
    {
        public const double Frame = 1.0 / 30;
        public static readonly QuestionBank Bank = QuizFixture.Bank(perDifficulty: 6, categories: 5);

        public static ScamQuizGame NewGame(int level = 1, int players = 2, int seed = 5, QuestionBank bank = null,
            IEnumerable<string> recent = null)
        {
            return new ScamQuizGame(bank ?? Bank, ScamQuizDifficulty.SettingsFor(level, players), seed, recent);
        }

        public static QuizInput[] Hands(bool first, bool second, int firstPanel = -1, int secondPanel = -1) =>
            new[] { new QuizInput(first, firstPanel), new QuizInput(second, secondPanel) };

        public static void Idle(ScamQuizGame game, double seconds, bool first = true, bool second = true)
        {
            for (double t = 0; t < seconds - 1e-9; t += Frame) game.Step(Frame, Hands(first, second));
        }

        /// <summary>Both hands appear, which starts a two-player round.</summary>
        public static ScamQuizGame Started(int level = 1, int seed = 5)
        {
            ScamQuizGame game = NewGame(level, 2, seed);
            game.Step(Frame, Hands(true, true));
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            return game;
        }

        /// <summary>A panel that is right (or wrong) in the current question.</summary>
        public static int PanelFor(ScamQuizGame game, bool correct)
        {
            for (int i = 0; i < game.Panels.Count; i++)
                if ((i == game.CorrectPanel) == correct) return i;
            throw new InvalidOperationException("No such panel.");
        }

        /// <summary>One player holds a panel until they have answered; the other hand stays up but idle.</summary>
        public static void Choose(ScamQuizGame game, int player, bool correct, int limitFrames = 400)
        {
            int panel = PanelFor(game, correct);
            for (int i = 0; i < limitFrames && game.Phase == QuizPhase.Asking && !game.Players[player].Answered; i++)
            {
                QuizInput[] inputs = Hands(true, true);
                inputs[player].Panel = panel;
                game.Step(Frame, inputs);
            }
        }

        /// <summary>Both players think for a while, then hold their choices together.</summary>
        public static void ChooseBoth(ScamQuizGame game, bool firstCorrect, bool secondCorrect, double thinkSeconds = 0)
        {
            Idle(game, thinkSeconds);
            int first = PanelFor(game, firstCorrect), second = PanelFor(game, secondCorrect);
            for (int i = 0; i < 400 && game.Phase == QuizPhase.Asking; i++)
                game.Step(Frame, Hands(true, true, first, second));
        }

        /// <summary>Let the explanation run out.</summary>
        public static void FinishFeedback(ScamQuizGame game)
        {
            for (int i = 0; i < 1000 && game.Phase == QuizPhase.Feedback; i++) game.Step(0.1, Hands(true, true));
        }

        /// <summary>Play every question of a round; answers[i] says whether each player is right in question i.</summary>
        public static void PlayRound(ScamQuizGame game, Func<int, bool> first, Func<int, bool> second, double think = 0)
        {
            int guard = 0;
            while (game.Phase != QuizPhase.Complete && guard++ < 100)
            {
                int question = game.QuestionIndex;
                if (game.Phase == QuizPhase.Asking) ChooseBoth(game, first(question), second(question), think);
                FinishFeedback(game);
            }
            Assert.AreEqual(QuizPhase.Complete, game.Phase);
        }
    }

    public sealed class ScamQuizGameTests
    {
        private const double Frame = QuizDriver.Frame;

        // ---- the last question --------------------------------------------------------------------------

        [Test]
        public void TheLastQuestionOfARoundIsAFinalQuestionAtEveryLevel()
        {
            QuestionBank bank = QuizFixture.Bank(perDifficulty: 6, finalQuestions: 6);
            for (int level = 1; level <= 5; level++)
            {
                ScamQuizGame game = QuizDriver.NewGame(level, seed: level, bank: bank);
                game.Step(Frame, QuizDriver.Hands(true, true));
                Assert.AreEqual(5, game.QuestionCount);
                var asked = new System.Collections.Generic.List<QuizQuestion>();
                while (game.Phase != QuizPhase.Complete)
                {
                    if (game.Phase == QuizPhase.Asking) { asked.Add(game.CurrentQuestion); QuizDriver.ChooseBoth(game, true, true); }
                    QuizDriver.FinishFeedback(game);
                }
                Assert.AreEqual(5, asked.Count, "level " + level);
                Assert.AreEqual(QuestionBank.FinalCategory, asked[4].Category);
                Assert.IsTrue(asked.Take(4).All(q => q.Category != QuestionBank.FinalCategory));
            }
        }

        [Test]
        public void ABankMadeOnlyOfFinalQuestionsCannotStartARound()
        {
            string onlyFinals = QuizFixture.BankJson(perDifficulty: 0, finalQuestions: 3);
            Assert.Throws<InvalidOperationException>(() =>
                new ScamQuizGame(QuestionBank.Parse(onlyFinals), ScamQuizDifficulty.SettingsFor(1), 1));
        }

        // ---- joining ----------------------------------------------------------------------------------

        [Test]
        public void TheRoundWaitsForBothPlayersThenStarts()
        {
            ScamQuizGame game = QuizDriver.NewGame();
            game.Step(Frame, QuizDriver.Hands(true, false));
            Assert.AreEqual(QuizPhase.Waiting, game.Phase);
            Assert.IsTrue(game.Players[0].Joined);
            Assert.IsFalse(game.Players[1].Joined);
            game.Step(Frame, QuizDriver.Hands(false, true));
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            Assert.IsNotNull(game.CurrentQuestion);
            Assert.AreEqual(5, game.QuestionCount);
            Assert.AreEqual(0, game.QuestionIndex);
            Assert.IsTrue(game.Players[0].Active && game.Players[1].Active);
        }

        [Test]
        public void StartNowBeginsWithWhoeverHasJoined()
        {
            ScamQuizGame game = QuizDriver.NewGame();
            Assert.IsFalse(game.StartNow(), "nobody has joined yet");
            game.Step(Frame, QuizDriver.Hands(false, true));
            Assert.IsTrue(game.StartNow());
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            Assert.IsFalse(game.Players[0].Active);
            Assert.IsTrue(game.Players[1].Active);
        }

        [Test]
        public void ASinglePlayerGameStartsWithOneHandAndHasNoSecondPlayer()
        {
            ScamQuizGame game = QuizDriver.NewGame(players: 1);
            game.Step(Frame, new[] { new QuizInput(true, -1) });
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            Assert.AreEqual(1, game.PlayerCount);
            Assert.Throws<ArgumentOutOfRangeException>(() => game.Stats(1));
            for (int i = 0; i < 100 && !game.Players[0].Answered; i++)
                game.Step(Frame, new[] { new QuizInput(true, game.CorrectPanel) });
            Assert.IsTrue(game.Players[0].Answered);
            Assert.AreEqual(QuizPhase.Feedback, game.Phase); // one player is enough to settle the question
        }

        // ---- the answers on screen ----------------------------------------------------------------------

        [Test]
        public void LowLevelsShowTwoAnswersAndAlwaysIncludeTheRightOne()
        {
            for (int seed = 0; seed < 20; seed++)
            {
                ScamQuizGame game = QuizDriver.Started(level: 1, seed: seed);
                Assert.AreEqual(2, game.Panels.Count);
                Assert.AreEqual(game.CurrentQuestion.Correct, game.Panels[game.CorrectPanel].OriginalIndex);
                Assert.AreEqual(game.CurrentQuestion.Choices[game.CurrentQuestion.Correct], game.Panels[game.CorrectPanel].Text);
                Assert.AreEqual(2, game.Panels.Select(p => p.OriginalIndex).Distinct().Count());
            }
        }

        [Test]
        public void HigherLevelsShowMoreAnswers()
        {
            Assert.AreEqual(3, QuizDriver.Started(level: 3).Panels.Count);
            ScamQuizGame four = QuizDriver.Started(level: 4);
            Assert.AreEqual(4, four.Panels.Count);
            Assert.AreEqual(4, four.Panels.Select(p => p.OriginalIndex).Distinct().Count());
        }

        [Test]
        public void TheRightAnswerIsNotAlwaysInTheSamePlace()
        {
            var positions = new HashSet<int>();
            for (int seed = 0; seed < 40; seed++) positions.Add(QuizDriver.Started(level: 1, seed: seed).CorrectPanel);
            CollectionAssert.AreEquivalent(new[] { 0, 1 }, positions);
        }

        // ---- choosing by holding ------------------------------------------------------------------------

        [Test]
        public void HoldingAnAnswerLongEnoughChoosesIt()
        {
            ScamQuizGame game = QuizDriver.Started(); // level 1: hold for 1.2 s
            for (int i = 0; i < 30; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1));
            Assert.IsFalse(game.Players[0].Answered, "one second is not enough");
            Assert.AreEqual(0, game.Players[0].DwellPanel);
            Assert.AreEqual(1.0 / 1.2, game.Players[0].DwellProgress, 0.02);
            for (int i = 0; i < 10; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1));
            Assert.IsTrue(game.Players[0].Answered);
            Assert.AreEqual(0, game.Players[0].SelectedPanel);
            Assert.IsFalse(game.Players[1].Answered, "the other player has not chosen");
        }

        [Test]
        public void LeavingDrainsTheProgressAndMovingToAnotherAnswerStartsAgain()
        {
            ScamQuizGame game = QuizDriver.Started();
            for (int i = 0; i < 18; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1)); // 0.6 s
            double held = game.Players[0].DwellProgress;
            Assert.AreEqual(0.5, held, 0.03);
            game.Step(Frame, QuizDriver.Hands(true, true, 1, -1));
            Assert.AreEqual(1, game.Players[0].DwellPanel);
            Assert.Less(game.Players[0].DwellProgress, 0.1, "progress does not carry over to a different answer");
            for (int i = 0; i < 12; i++) game.Step(Frame, QuizDriver.Hands(true, true, 1, -1)); // 0.4 s
            double before = game.Players[0].DwellProgress;
            for (int i = 0; i < 6; i++) game.Step(Frame, QuizDriver.Hands(true, true, -1, -1)); // off the answers for 0.2 s
            Assert.Less(game.Players[0].DwellProgress, before);
            Assert.AreEqual(before - 0.2 * 1.5 / 1.2, game.Players[0].DwellProgress, 0.03); // drains 1.5x as fast
        }

        [Test]
        public void ALostHandStopsTheHoldAndANewAnswerNeedsAFreshOne()
        {
            ScamQuizGame game = QuizDriver.Started();
            for (int i = 0; i < 24; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1));
            for (int i = 0; i < 40; i++) game.Step(Frame, QuizDriver.Hands(false, true));
            Assert.IsFalse(game.Players[0].Answered);
            Assert.AreEqual(0, game.Players[0].DwellProgress);
        }

        [Test]
        public void ALockedAnswerCannotBeChanged()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.Choose(game, 0, correct: false);
            int chosen = game.Players[0].SelectedPanel;
            for (int i = 0; i < 90; i++) game.Step(Frame, QuizDriver.Hands(true, true, QuizDriver.PanelFor(game, true), -1));
            Assert.AreEqual(chosen, game.Players[0].SelectedPanel);
            Assert.AreEqual(1, game.Stats(0).Total);
            Assert.AreEqual(1, game.Stats(0).Wrong);
        }

        [Test]
        public void AFrameHitchCannotChooseAnAnswer()
        {
            ScamQuizGame game = QuizDriver.Started();
            game.Step(5.0, QuizDriver.Hands(true, true, 0, 0));
            Assert.IsFalse(game.Players[0].Answered);
            Assert.AreEqual(ScamQuizGame.MaxStepSeconds, game.QuestionSeconds - game.TimeRemaining, 1e-9);
        }

        [Test]
        public void BadTimeStepsAndBadInputsAreHandled()
        {
            ScamQuizGame game = QuizDriver.Started();
            double before = game.TimeRemaining;
            game.Step(double.NaN, QuizDriver.Hands(true, true, 0, 0));
            game.Step(-1, QuizDriver.Hands(true, true, 0, 0));
            game.Step(double.PositiveInfinity, QuizDriver.Hands(true, true, 0, 0));
            Assert.AreEqual(before, game.TimeRemaining);
            Assert.Throws<ArgumentNullException>(() => game.Step(Frame, null));
            Assert.Throws<ArgumentException>(() => game.Step(Frame, new[] { new QuizInput(true, 0) }));
            game.Step(Frame, QuizDriver.Hands(true, true, 99, -5)); // panels that do not exist are just "no panel"
            Assert.AreEqual(-1, game.Players[0].DwellPanel);
        }

        // ---- answering ----------------------------------------------------------------------------------

        [Test]
        public void RightAndWrongAnswersAreRecordedWithTheOriginalChoiceNumber()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizQuestion question = game.CurrentQuestion;
            QuizDriver.Choose(game, 0, correct: true);
            QuizDriver.Choose(game, 1, correct: false);
            Assert.AreEqual(QuizPhase.Feedback, game.Phase);
            Assert.IsTrue(game.Players[0].IsCorrect);
            Assert.IsFalse(game.Players[1].IsCorrect);
            ScamQuizStats first = game.Stats(0), second = game.Stats(1);
            Assert.AreEqual((1, 0, 0), (first.Correct, first.Wrong, first.Skipped));
            Assert.AreEqual((0, 1, 0), (second.Correct, second.Wrong, second.Skipped));
            Assert.AreEqual(question.Id, first.Responses[0].QuestionId);
            Assert.AreEqual(question.Correct, first.Responses[0].Selected);
            Assert.IsTrue(first.Responses[0].IsCorrect);
            Assert.AreNotEqual(question.Correct, second.Responses[0].Selected);
            Assert.GreaterOrEqual(second.Responses[0].Selected, 0);
            Assert.LessOrEqual(second.Responses[0].Selected, 3);
            Assert.IsFalse(second.Responses[0].IsCorrect);
        }

        [Test]
        public void TheQuestionWaitsForTheSecondPlayerThenShowsTheExplanation()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.Choose(game, 0, correct: true);
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            QuizDriver.Choose(game, 1, correct: true);
            Assert.AreEqual(QuizPhase.Feedback, game.Phase);
            Assert.AreEqual(6, game.FeedbackRemaining, 1e-9);
            Assert.IsNotNull(game.CurrentQuestion);
            Assert.AreEqual(game.CurrentQuestion.Correct, game.Panels[game.CorrectPanel].OriginalIndex);
        }

        [Test]
        public void TheClockStopsWhileNobodysHandIsTracked()
        {
            ScamQuizGame game = QuizDriver.Started();
            double before = game.TimeRemaining;
            QuizDriver.Idle(game, 5, first: false, second: false);
            Assert.AreEqual(before, game.TimeRemaining, 1e-9);
            QuizDriver.Idle(game, 2, first: false, second: true);
            Assert.AreEqual(before - 2, game.TimeRemaining, 0.1, "it runs while either hand is tracked");
        }

        [Test]
        public void ResponseTimeIsTrackedTimeNotWallClockTime()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.Idle(game, 3, first: false, second: false); // thinking with hands down is free
            QuizDriver.Idle(game, 2);
            QuizDriver.Choose(game, 0, correct: true); // 1.2 s of holding
            double seconds = game.Stats(0).AverageResponseTime.Value;
            Assert.AreEqual(3.2, seconds, 0.12);
            Assert.AreEqual((int)Math.Round(seconds * 1000), game.Stats(0).Responses[0].ResponseMs);
        }

        [Test]
        public void WhenTimeRunsOutAnyoneWhoHasNotAnsweredIsSkipped()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.Choose(game, 0, correct: true);
            for (int i = 0; i < 2000 && game.Phase == QuizPhase.Asking; i++) game.Step(0.1, QuizDriver.Hands(true, true));
            Assert.AreEqual(QuizPhase.Feedback, game.Phase);
            Assert.IsTrue(game.Players[1].TimedOut);
            Assert.IsFalse(game.Players[0].TimedOut);
            Assert.AreEqual((0, 0, 1), (game.Stats(1).Correct, game.Stats(1).Wrong, game.Stats(1).Skipped));
            Assert.AreEqual(-1, game.Stats(1).Responses[0].Selected);
            Assert.IsNull(game.Stats(1).Responses[0].ResponseMs);
            Assert.IsNull(game.Stats(1).AverageResponseTime, "a skipped question has no response time");
            Assert.AreEqual(0, game.Stats(1).Score);
        }

        [Test]
        public void NobodyAnsweringSkipsBothPlayers()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.Idle(game, game.QuestionSeconds + 1);
            Assert.AreEqual(QuizPhase.Feedback, game.Phase);
            Assert.AreEqual(1, game.Stats(0).Skipped);
            Assert.AreEqual(1, game.Stats(1).Skipped);
        }

        // ---- the round ----------------------------------------------------------------------------------

        [Test]
        public void ALongExplanationStaysUpLongerWithinACap()
        {
            // 300 extra characters is about 9 s of reading at 0.03 s a character: more than the 6 s minimum.
            ScamQuizGame longer = QuizDriver.NewGame(bank: QuizFixture.Bank(perDifficulty: 6, extraExplanationCharacters: 300));
            longer.Step(Frame, QuizDriver.Hands(true, true));
            QuizDriver.ChooseBoth(longer, true, true);
            Assert.AreEqual((300 + "Because q1-0.".Length) * 0.03, longer.FeedbackRemaining, 0.2);
            Assert.Greater(longer.FeedbackRemaining, 8);

            // The cap wins over a very long explanation.
            ScamQuizSettings capped = ScamQuizDifficulty.SettingsFor(1);
            capped.MaxExplanationSeconds = 8;
            var game = new ScamQuizGame(QuizFixture.Bank(perDifficulty: 6, extraExplanationCharacters: 400), capped, 1);
            game.Step(Frame, QuizDriver.Hands(true, true));
            QuizDriver.ChooseBoth(game, true, true);
            Assert.AreEqual(8, game.FeedbackRemaining, 1e-9);

            // A short explanation still gets the minimum.
            ScamQuizGame shorter = QuizDriver.Started();
            QuizDriver.ChooseBoth(shorter, true, true);
            Assert.AreEqual(6, shorter.FeedbackRemaining, 1e-9);
        }

        [Test]
        public void TheExplanationRunsOutAndTheNextQuestionStarts()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizQuestion first = game.CurrentQuestion;
            QuizDriver.ChooseBoth(game, true, true);
            QuizDriver.Idle(game, 5);
            Assert.AreEqual(QuizPhase.Feedback, game.Phase);
            QuizDriver.FinishFeedback(game);
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            Assert.AreEqual(1, game.QuestionIndex);
            Assert.AreNotEqual(first.Id, game.CurrentQuestion.Id);
            Assert.IsFalse(game.Players[0].Answered || game.Players[1].Answered);
            Assert.AreEqual(game.QuestionSeconds, game.TimeRemaining, 1e-9);
        }

        [Test]
        public void AfterTheLastQuestionTheRoundIsComplete()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.PlayRound(game, q => true, q => q % 2 == 0);
            Assert.AreEqual(QuizPhase.Complete, game.Phase);
            Assert.IsNull(game.CurrentQuestion);
            Assert.AreEqual(5, game.QuestionIndex);
            Assert.AreEqual(5, game.Stats(0).Total);
            Assert.AreEqual(5, game.Stats(0).Correct);
            Assert.AreEqual(5, game.Stats(0).BestStreak);
            Assert.AreEqual(3, game.Stats(1).Correct); // questions 0, 2 and 4
            Assert.AreEqual(2, game.Stats(1).Wrong);
            Assert.AreEqual(1, game.Stats(1).BestStreak);
            Assert.AreEqual(0.6, game.Stats(1).Score.Value, 1e-9);
            Assert.AreEqual(5, game.AskedQuestionIds.Distinct().Count());
            Assert.IsTrue(game.Stats(0).Responses.Select(r => r.QuestionId).SequenceEqual(game.AskedQuestionIds));
        }

        [Test]
        public void StatisticsAreReportedAsNullWhenThereAreNoSamples()
        {
            ScamQuizGame game = QuizDriver.Started();
            ScamQuizStats stats = game.Stats(0);
            Assert.IsNull(stats.Score);
            Assert.IsNull(stats.AverageResponseTime);
            Assert.IsNull(stats.FastestResponse);
            Assert.IsNull(stats.SlowestResponse);
        }

        [Test]
        public void FastestAndSlowestResponsesAreTracked()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.ChooseBoth(game, true, true, thinkSeconds: 1);
            QuizDriver.FinishFeedback(game);
            QuizDriver.ChooseBoth(game, true, true, thinkSeconds: 6);
            ScamQuizStats stats = game.Stats(0);
            Assert.AreEqual(2.2, stats.FastestResponse.Value, 0.15);
            Assert.AreEqual(7.2, stats.SlowestResponse.Value, 0.15);
            Assert.AreEqual((stats.FastestResponse.Value + stats.SlowestResponse.Value) / 2, stats.AverageResponseTime.Value, 1e-9);
        }

        [Test]
        public void ASkippedQuestionBreaksTheStreakButIsNotAWrongAnswer()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.ChooseBoth(game, true, true);
            QuizDriver.FinishFeedback(game);
            QuizDriver.Idle(game, game.QuestionSeconds + 1);
            QuizDriver.FinishFeedback(game);
            QuizDriver.ChooseBoth(game, true, true);
            ScamQuizStats stats = game.Stats(0);
            Assert.AreEqual((2, 0, 1), (stats.Correct, stats.Wrong, stats.Skipped));
            Assert.AreEqual(1, stats.BestStreak);
            Assert.AreEqual(1, stats.CurrentStreak);
        }

        [Test]
        public void ARoundEndsEarlyWhenTheBankHasFewerQuestionsThanTheRoundLength()
        {
            QuestionBank tiny = QuizFixture.Bank(perDifficulty: 1, categories: 1); // one question per difficulty
            ScamQuizGame game = QuizDriver.NewGame(level: 1, bank: tiny);
            game.Step(Frame, QuizDriver.Hands(true, true));
            Assert.AreEqual(1, game.QuestionCount);
            QuizDriver.PlayRound(game, q => true, q => true);
            Assert.AreEqual(QuizPhase.Complete, game.Phase);
        }

        // ---- the summary and playing again ---------------------------------------------------------------

        [Test]
        public void PlayingAgainNeedsTheButtonToBeLeftFirstThenHeld()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.PlayRound(game, q => true, q => true);
            // A cursor left on the button from the last answer cannot restart the round.
            for (int i = 0; i < 120; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1));
            Assert.AreEqual(QuizPhase.Complete, game.Phase);
            game.Step(Frame, QuizDriver.Hands(true, true, -1, -1)); // leave it once
            for (int i = 0; i < 30; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1));
            Assert.AreEqual(QuizPhase.Complete, game.Phase, "one second is not enough");
            Assert.Greater(game.Players[0].DwellProgress, 0.5);
            for (int i = 0; i < 10; i++) game.Step(Frame, QuizDriver.Hands(true, true, 0, -1));
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            Assert.AreEqual(0, game.QuestionIndex);
            Assert.AreEqual(0, game.Stats(0).Total, "statistics start again");
        }

        [Test]
        public void RestartBeginsANewRoundAtOnceWithTheSamePlayers()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.PlayRound(game, q => true, q => false);
            game.Restart();
            Assert.AreEqual(QuizPhase.Asking, game.Phase);
            Assert.AreEqual(0, game.QuestionIndex);
            Assert.IsTrue(game.Players[0].Active && game.Players[1].Active);
            Assert.AreEqual(0, game.Stats(1).Total);
        }

        [Test]
        public void ANewRoundAvoidsTheQuestionsJustAsked()
        {
            ScamQuizGame game = QuizDriver.Started(level: 5); // 20 questions to choose from
            string[] first = game.AskedQuestionIds.ToArray();
            QuizDriver.PlayRound(game, q => true, q => true);
            game.Restart();
            Assert.IsFalse(game.AskedQuestionIds.Any(id => first.Contains(id)));
        }

        [Test]
        public void QuestionsFromAnEarlierSessionAreAvoidedToo()
        {
            string[] earlier = QuizDriver.Bank.Questions.Where(q => q.Difficulty <= 2).Take(3).Select(q => q.Id).ToArray();
            ScamQuizGame game = QuizDriver.NewGame(level: 2, seed: 9, recent: earlier);
            game.Step(Frame, QuizDriver.Hands(true, true));
            Assert.IsFalse(game.AskedQuestionIds.Any(id => earlier.Contains(id)));
        }

        // ---- changing the rules --------------------------------------------------------------------------

        [Test]
        public void NewRulesAreRefusedMidRoundAndAppliedBetweenRounds()
        {
            ScamQuizGame game = QuizDriver.NewGame();
            Assert.IsTrue(game.ApplySettings(ScamQuizDifficulty.SettingsFor(3))); // waiting: fine
            game.Step(Frame, QuizDriver.Hands(true, true));
            Assert.AreEqual(3, game.Panels.Count);
            Assert.IsFalse(game.ApplySettings(ScamQuizDifficulty.SettingsFor(5)), "mid-question");
            Assert.AreEqual(3, game.Level);
            QuizDriver.ChooseBoth(game, true, true);
            Assert.IsFalse(game.ApplySettings(ScamQuizDifficulty.SettingsFor(5)), "during the explanation");
            QuizDriver.PlayRound(game, q => true, q => true);
            Assert.IsTrue(game.ApplySettings(ScamQuizDifficulty.SettingsFor(5)));
            game.Restart();
            Assert.AreEqual(4, game.Panels.Count);
            Assert.AreEqual(5, game.Level);
        }

        [Test]
        public void ChangingTheNumberOfPlayersSendsTheGameBackToWaiting()
        {
            ScamQuizGame game = QuizDriver.Started();
            QuizDriver.PlayRound(game, q => true, q => true);
            Assert.IsTrue(game.ApplySettings(ScamQuizDifficulty.SettingsFor(1, players: 1)));
            Assert.AreEqual(QuizPhase.Waiting, game.Phase);
            Assert.AreEqual(1, game.PlayerCount);
        }

        [Test]
        public void InvalidSettingsAndAnUnusableBankAreRefused()
        {
            ScamQuizSettings Make(Action<ScamQuizSettings> change)
            {
                ScamQuizSettings s = ScamQuizDifficulty.SettingsFor(1);
                change(s);
                return s;
            }
            var bad = new[]
            {
                Make(s => s.Players = 0), Make(s => s.Players = 3), Make(s => s.RoundLength = 0), Make(s => s.RoundLength = 21),
                Make(s => s.QuestionSeconds = 0), Make(s => s.QuestionSeconds = double.NaN), Make(s => s.ChoiceCount = 1),
                Make(s => s.ChoiceCount = 5), Make(s => s.DwellSeconds = 0), Make(s => s.DwellSeconds = 30),
                Make(s => s.DwellDecayFactor = 0), Make(s => s.ExplanationSeconds = -1), Make(s => s.Level = 6),
                Make(s => s.ExplanationSecondsPerCharacter = -1), Make(s => s.MaxExplanationSeconds = double.NaN),
                Make(s => s.MaxQuestionDifficulty = 0)
            };
            foreach (ScamQuizSettings settings in bad)
                Assert.Throws<ArgumentException>(() => new ScamQuizGame(QuizDriver.Bank, settings, 1));
            Assert.Throws<ArgumentNullException>(() => new ScamQuizGame(null, ScamQuizDifficulty.SettingsFor(1), 1));
            Assert.Throws<ArgumentNullException>(() => new ScamQuizGame(QuizDriver.Bank, null, 1));
            // A bank with nothing easy enough for level 1 cannot start a level-1 game.
            string hard = QuizFixture.BankJson(1).Replace("\"difficulty\":1", "\"difficulty\":3");
            Assert.Throws<InvalidOperationException>(() =>
                new ScamQuizGame(QuestionBank.Parse(hard), ScamQuizDifficulty.SettingsFor(1), 1));
        }

        [Test]
        public void TheSameSeedPlaysTheSameRound()
        {
            string[] Round(int seed) => QuizDriver.Started(level: 3, seed: seed).AskedQuestionIds.ToArray();
            CollectionAssert.AreEqual(Round(4), Round(4));
        }
    }
}
