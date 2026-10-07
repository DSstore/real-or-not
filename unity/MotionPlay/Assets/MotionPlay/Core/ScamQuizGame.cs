using System;
using System.Collections.Generic;
using System.Linq;

namespace MotionPlay.Games
{
    public enum QuizPhase
    {
        /// <summary>Waiting for the players to show their hands.</summary>
        Waiting,
        /// <summary>A question is up and the players are choosing.</summary>
        Asking,
        /// <summary>The question is settled; the right answer and the explanation are showing.</summary>
        Feedback,
        /// <summary>The round is over and the summary is showing.</summary>
        Complete
    }

    /// <summary>What one player's hand is doing this frame, as worked out by the screen layer.</summary>
    public struct QuizInput
    {
        /// <summary>True when the player's hand is tracked and the cursor position is valid.</summary>
        public bool Tracking;
        /// <summary>
        /// The answer panel under the cursor (an index into <see cref="ScamQuizGame.Panels"/>), or -1 for none. On
        /// the summary screen, 0 means the "Play again" button.
        /// </summary>
        public int Panel;

        public QuizInput(bool tracking, int panel) { Tracking = tracking; Panel = panel; }
    }

    /// <summary>Tunable quiz rules. Times are seconds.</summary>
    public sealed class ScamQuizSettings
    {
        /// <summary>People playing at once, 1 or 2.</summary>
        public int Players { get; set; } = 2;
        public int RoundLength { get; set; } = 5;
        /// <summary>Tracked time allowed per question; the clock stops while nobody's hand is tracked.</summary>
        public double QuestionSeconds { get; set; } = 25;
        /// <summary>How many answers to show (2 to 4). The right one is always among them.</summary>
        public int ChoiceCount { get; set; } = 2;
        /// <summary>Time a cursor must stay on an answer to choose it.</summary>
        public double DwellSeconds { get; set; } = 1.2;
        /// <summary>Dwell progress drains this many times faster than it fills when the cursor leaves.</summary>
        public double DwellDecayFactor { get; set; } = 1.5;
        /// <summary>The least time the right answer and the explanation stay up.</summary>
        public double ExplanationSeconds { get; set; } = 6;
        /// <summary>Extra reading time for a long explanation: this many seconds per character, up to <see cref="MaxExplanationSeconds"/>.</summary>
        public double ExplanationSecondsPerCharacter { get; set; } = 0.03;
        /// <summary>The most time an explanation may stay up, however long it is.</summary>
        public double MaxExplanationSeconds { get; set; } = 15;
        /// <summary>The level these rules belong to (1 to 5).</summary>
        public int Level { get; set; } = ScamQuizDifficulty.DefaultLevel;
        /// <summary>Hardest question that may be asked (1 to 5).</summary>
        public int MaxQuestionDifficulty { get; set; } = 1;

        internal void Validate()
        {
            if (Players < 1 || Players > ScamQuizGame.MaxPlayers) throw new ArgumentException("Players must be 1 or 2.");
            if (RoundLength < 1 || RoundLength > QuizPicker.MaxRoundLength)
                throw new ArgumentException("RoundLength must be from 1 to " + QuizPicker.MaxRoundLength + ".");
            if (!Positive(QuestionSeconds)) throw new ArgumentException("QuestionSeconds must be finite and positive.");
            if (ChoiceCount < 2 || ChoiceCount > QuestionBank.ChoiceCount)
                throw new ArgumentException("ChoiceCount must be from 2 to " + QuestionBank.ChoiceCount + ".");
            if (!Positive(DwellSeconds) || DwellSeconds >= QuestionSeconds)
                throw new ArgumentException("DwellSeconds must be finite, positive and shorter than QuestionSeconds.");
            if (!Positive(DwellDecayFactor)) throw new ArgumentException("DwellDecayFactor must be finite and positive.");
            if (double.IsNaN(ExplanationSeconds) || double.IsInfinity(ExplanationSeconds) || ExplanationSeconds < 0)
                throw new ArgumentException("ExplanationSeconds must be finite and not negative.");
            if (double.IsNaN(ExplanationSecondsPerCharacter) || double.IsInfinity(ExplanationSecondsPerCharacter) || ExplanationSecondsPerCharacter < 0)
                throw new ArgumentException("ExplanationSecondsPerCharacter must be finite and not negative.");
            if (double.IsNaN(MaxExplanationSeconds) || double.IsInfinity(MaxExplanationSeconds) || MaxExplanationSeconds < 0)
                throw new ArgumentException("MaxExplanationSeconds must be finite and not negative.");
            if (Level < ScamQuizDifficulty.MinLevel || Level > ScamQuizDifficulty.MaxLevel)
                throw new ArgumentException("Level must be from 1 to 5.");
            if (MaxQuestionDifficulty < QuestionBank.MinDifficulty || MaxQuestionDifficulty > QuestionBank.MaxDifficulty)
                throw new ArgumentException("MaxQuestionDifficulty must be from 1 to 5.");
        }

        private static bool Positive(double value) => !double.IsNaN(value) && !double.IsInfinity(value) && value > 0;
    }

    /// <summary>One answer as shown on screen.</summary>
    public sealed class QuizChoice
    {
        /// <summary>Index into the question's written choices; this is what is recorded and sent.</summary>
        public int OriginalIndex { get; }
        public string Text { get; }
        internal QuizChoice(int originalIndex, string text) { OriginalIndex = originalIndex; Text = text; }
    }

    /// <summary>What one player is doing in the current question. Updated by the game; read-only to everyone else.</summary>
    public sealed class QuizPlayerState
    {
        /// <summary>Taking part in this round.</summary>
        public bool Active { get; internal set; }
        /// <summary>Has shown a hand while waiting to start.</summary>
        public bool Joined { get; internal set; }
        public bool Answered { get; internal set; }
        /// <summary>The panel chosen (an index into the shown panels), or -1.</summary>
        public int SelectedPanel { get; internal set; } = -1;
        public bool IsCorrect { get; internal set; }
        /// <summary>The panel being held, or -1.</summary>
        public int DwellPanel { get; internal set; } = -1;
        /// <summary>Share (0 to 1) of the hold completed on <see cref="DwellPanel"/>.</summary>
        public double DwellProgress { get; internal set; }
        /// <summary>Time ran out before this player answered.</summary>
        public bool TimedOut { get; internal set; }
        internal double Dwell;
        internal bool RestartArmed;

        internal void ResetForQuestion()
        {
            Answered = false; SelectedPanel = -1; IsCorrect = false; DwellPanel = -1; DwellProgress = 0;
            TimedOut = false; Dwell = 0;
        }
    }

    /// <summary>
    /// Scam quiz rules for one or two players answering the same questions at the same time. Everyone chooses by
    /// holding their cursor on an answer; a question ends when every player has answered or the time runs out, then
    /// the right answer and an explanation show. Contains no engine types so the same code runs in Unity and in the
    /// standalone test harness. The screen layer turns each cursor into a panel number (see <see cref="QuizInput"/>)
    /// and draws what this class reports. Per-player statistics live in <see cref="ScamQuizStats"/>.
    /// </summary>
    public sealed class ScamQuizGame
    {
        /// <summary>Largest time step accepted, so a frame hitch cannot answer a question.</summary>
        public const double MaxStepSeconds = 0.1;
        /// <summary>Most people who can play at once (they share one camera, one zone each).</summary>
        public const int MaxPlayers = 2;

        private ScamQuizSettings settings;
        private readonly QuestionBank bank;
        private readonly Random random;
        private readonly RecentQuestionList recent;
        private readonly QuizPlayerState[] players;
        private readonly ScamQuizStats[] stats;
        private List<QuizQuestion> questions = new List<QuizQuestion>();
        private List<QuizChoice> panels = new List<QuizChoice>();
        private double elapsed;
        private double feedbackRemaining;

        public ScamQuizGame(QuestionBank bank, ScamQuizSettings settings, int seed, IEnumerable<string> recentQuestionIds = null)
        {
            this.bank = bank ?? throw new ArgumentNullException(nameof(bank));
            this.settings = settings ?? throw new ArgumentNullException(nameof(settings));
            settings.Validate();
            RequireQuestions(settings);
            random = new Random(seed);
            recent = new RecentQuestionList();
            if (recentQuestionIds != null) recent.Add(recentQuestionIds);
            players = new QuizPlayerState[ScamQuizGame.MaxPlayers];
            stats = new ScamQuizStats[ScamQuizGame.MaxPlayers];
            for (int i = 0; i < players.Length; i++) { players[i] = new QuizPlayerState(); stats[i] = new ScamQuizStats(); }
            Phase = QuizPhase.Waiting;
        }

        public QuizPhase Phase { get; private set; }
        public int PlayerCount => settings.Players;
        public int Level => settings.Level;
        public double QuestionSeconds => settings.QuestionSeconds;
        public double DwellSeconds => settings.DwellSeconds;
        public IReadOnlyList<QuizPlayerState> Players => Array.AsReadOnly(players);
        /// <summary>The question being asked or explained; null before the first one and after the last.</summary>
        public QuizQuestion CurrentQuestion { get; private set; }
        /// <summary>Zero-based index of the current question; equals <see cref="QuestionCount"/> when complete.</summary>
        public int QuestionIndex { get; private set; }
        public int QuestionCount => questions.Count;
        /// <summary>The answers on screen, in the order shown.</summary>
        public IReadOnlyList<QuizChoice> Panels => panels.AsReadOnly();
        /// <summary>Which panel is the right answer, or -1 when there is no question.</summary>
        public int CorrectPanel { get; private set; } = -1;
        /// <summary>Tracked time left on the current question.</summary>
        public double TimeRemaining => Phase == QuizPhase.Asking ? Math.Max(0, settings.QuestionSeconds - elapsed) : 0;
        /// <summary>Time left on the explanation.</summary>
        public double FeedbackRemaining => Phase == QuizPhase.Feedback ? Math.Max(0, feedbackRemaining) : 0;
        /// <summary>Ids of the questions chosen for the current round, in the order asked.</summary>
        public IEnumerable<string> AskedQuestionIds => questions.Select(question => question.Id);

        /// <summary>Statistics for one player's current (or just finished) round.</summary>
        public ScamQuizStats Stats(int player) => stats[CheckPlayer(player)];

        /// <summary>The statistics of every player taking part, for judging the round as a group.</summary>
        public IEnumerable<ScamQuizStats> ActiveStats() =>
            Enumerable.Range(0, settings.Players).Where(i => players[i].Active).Select(i => stats[i]);

        /// <summary>
        /// Advance by one frame. <paramref name="inputs"/> holds one entry per player; the screen layer fills in
        /// whether each hand is tracked and which panel its cursor is on.
        /// </summary>
        public void Step(double deltaSeconds, QuizInput[] inputs)
        {
            if (inputs == null) throw new ArgumentNullException(nameof(inputs));
            if (inputs.Length < settings.Players) throw new ArgumentException("One input per player is required.", nameof(inputs));
            if (double.IsNaN(deltaSeconds) || double.IsInfinity(deltaSeconds) || deltaSeconds < 0) return;
            double dt = Math.Min(deltaSeconds, MaxStepSeconds);

            switch (Phase)
            {
                case QuizPhase.Waiting: StepWaiting(inputs); break;
                case QuizPhase.Asking: StepAsking(dt, inputs); break;
                case QuizPhase.Feedback: StepFeedback(dt); break;
                default: StepComplete(dt, inputs); break;
            }
        }

        /// <summary>Begin a round now with the players who have shown a hand (for example from a keyboard shortcut).</summary>
        /// <returns>False if nobody has joined yet.</returns>
        public bool StartNow()
        {
            if (Phase == QuizPhase.Asking || Phase == QuizPhase.Feedback) return false;
            if (Phase == QuizPhase.Waiting && !players.Take(settings.Players).Any(player => player.Joined)) return false;
            StartRound();
            return true;
        }

        /// <summary>Begin a fresh round immediately with the same players, from the summary or by key.</summary>
        public void Restart()
        {
            if (!players.Take(settings.Players).Any(player => player.Active || player.Joined)) return;
            StartRound();
        }

        /// <summary>Swap in new rules, for example a different level. Refused mid-round so a question never changes under the players.</summary>
        /// <returns>True if applied; false while a round is in progress.</returns>
        public bool ApplySettings(ScamQuizSettings newSettings)
        {
            if (newSettings == null) throw new ArgumentNullException(nameof(newSettings));
            newSettings.Validate();
            RequireQuestions(newSettings);
            if (Phase == QuizPhase.Asking || Phase == QuizPhase.Feedback) return false;
            if (newSettings.Players != settings.Players)
            {
                // A different number of people: wait for their hands again rather than carry on with the old set.
                for (int i = 0; i < players.Length; i++) { players[i].Joined = false; players[i].Active = false; }
                Phase = QuizPhase.Waiting;
            }
            settings = newSettings;
            return true;
        }

        private void RequireQuestions(ScamQuizSettings forSettings)
        {
            if (bank.CountAtOrBelow(forSettings.MaxQuestionDifficulty) < 1)
                throw new InvalidOperationException("The question bank has no questions at difficulty " +
                                                    forSettings.MaxQuestionDifficulty + " or below.");
        }

        private int CheckPlayer(int player)
        {
            if (player < 0 || player >= settings.Players) throw new ArgumentOutOfRangeException(nameof(player));
            return player;
        }

        private void StepWaiting(QuizInput[] inputs)
        {
            for (int i = 0; i < settings.Players; i++)
                if (Usable(inputs[i])) players[i].Joined = true;
            if (players.Take(settings.Players).All(player => player.Joined)) StartRound();
        }

        private void StartRound()
        {
            for (int i = 0; i < players.Length; i++)
            {
                bool takesPart = i < settings.Players && (players[i].Joined || players[i].Active);
                players[i].Active = takesPart;
                players[i].Joined = takesPart;
                players[i].ResetForQuestion();
                players[i].RestartArmed = false;
                stats[i].Reset();
            }
            questions = QuizPicker.Pick(bank, settings.MaxQuestionDifficulty, settings.RoundLength, recent.Ids, random).ToList();
            QuestionIndex = 0;
            BeginQuestion();
        }

        private void BeginQuestion()
        {
            CurrentQuestion = questions[QuestionIndex];
            // The right answer plus enough wrong ones, in a random order, so every question works at every level.
            var wrong = Enumerable.Range(0, CurrentQuestion.Choices.Count).Where(i => i != CurrentQuestion.Correct).ToList();
            Shuffle(wrong);
            var shown = new List<int> { CurrentQuestion.Correct };
            shown.AddRange(wrong.Take(settings.ChoiceCount - 1));
            Shuffle(shown);
            panels = shown.Select(index => new QuizChoice(index, CurrentQuestion.Choices[index])).ToList();
            CorrectPanel = shown.IndexOf(CurrentQuestion.Correct);
            elapsed = 0;
            foreach (QuizPlayerState player in players) player.ResetForQuestion();
            Phase = QuizPhase.Asking;
        }

        private void StepAsking(double dt, QuizInput[] inputs)
        {
            // The clock runs only while somebody's hand is tracked, so putting your hand down to think is free.
            bool anyTracked = false;
            for (int i = 0; i < settings.Players; i++)
                if (players[i].Active && Usable(inputs[i])) anyTracked = true;
            if (anyTracked) elapsed += dt;

            for (int i = 0; i < settings.Players; i++)
            {
                QuizPlayerState player = players[i];
                if (!player.Active || player.Answered) continue;
                bool onPanel = Usable(inputs[i]) && inputs[i].Panel >= 0 && inputs[i].Panel < panels.Count;
                if (onPanel)
                {
                    if (player.DwellPanel != inputs[i].Panel) { player.DwellPanel = inputs[i].Panel; player.Dwell = 0; }
                    player.Dwell += dt;
                }
                else
                {
                    player.Dwell = Math.Max(0, player.Dwell - dt * settings.DwellDecayFactor);
                    if (player.Dwell <= 0) player.DwellPanel = -1;
                }
                player.DwellProgress = Math.Min(1, player.Dwell / settings.DwellSeconds);
                if (player.DwellPanel >= 0 && player.Dwell >= settings.DwellSeconds) Answer(i, player.DwellPanel);
            }

            bool everyoneAnswered = players.Take(settings.Players).All(player => !player.Active || player.Answered);
            if (everyoneAnswered || elapsed >= settings.QuestionSeconds) Resolve();
        }

        private void Answer(int index, int panel)
        {
            QuizPlayerState player = players[index];
            player.Answered = true;
            player.SelectedPanel = panel;
            player.DwellProgress = 1;
            int selected = panels[panel].OriginalIndex;
            player.IsCorrect = selected == CurrentQuestion.Correct;
            stats[index].RecordAnswer(CurrentQuestion, selected, elapsed);
        }

        private void Resolve()
        {
            for (int i = 0; i < settings.Players; i++)
            {
                QuizPlayerState player = players[i];
                if (!player.Active || player.Answered) continue;
                player.TimedOut = true;
                player.DwellPanel = -1; player.DwellProgress = 0; player.Dwell = 0;
                stats[i].RecordSkip(CurrentQuestion);
            }
            // At least the minimum, longer for a long explanation so it can be read, but never beyond the cap.
            double reading = Math.Min(settings.MaxExplanationSeconds,
                                      CurrentQuestion.Explanation.Length * settings.ExplanationSecondsPerCharacter);
            feedbackRemaining = Math.Max(settings.ExplanationSeconds, reading);
            Phase = QuizPhase.Feedback;
        }

        private void StepFeedback(double dt)
        {
            feedbackRemaining -= dt;
            if (feedbackRemaining > 0) return;
            QuestionIndex++;
            if (QuestionIndex >= questions.Count) { Finish(); return; }
            BeginQuestion();
        }

        private void Finish()
        {
            recent.Add(AskedQuestionIds);
            CurrentQuestion = null;
            panels = new List<QuizChoice>();
            CorrectPanel = -1;
            QuestionIndex = questions.Count;
            foreach (QuizPlayerState player in players) { player.ResetForQuestion(); player.RestartArmed = false; }
            Phase = QuizPhase.Complete;
        }

        private void StepComplete(double dt, QuizInput[] inputs)
        {
            // Panel 0 is the "Play again" button. Each player must leave it once first, so a cursor left where the
            // last answer was cannot restart the round straight away.
            for (int i = 0; i < settings.Players; i++)
            {
                QuizPlayerState player = players[i];
                if (!player.Active) continue;
                bool onButton = Usable(inputs[i]) && inputs[i].Panel == 0;
                if (Usable(inputs[i]) && !onButton) player.RestartArmed = true;
                if (onButton && player.RestartArmed) { player.DwellPanel = 0; player.Dwell += dt; }
                else { player.Dwell = Math.Max(0, player.Dwell - dt * settings.DwellDecayFactor); if (player.Dwell <= 0) player.DwellPanel = -1; }
                if (!player.RestartArmed) player.Dwell = 0;
                player.DwellProgress = Math.Min(1, player.Dwell / settings.DwellSeconds);
                if (player.Dwell >= settings.DwellSeconds) { StartRound(); return; }
            }
        }

        private static bool Usable(QuizInput input) => input.Tracking;

        private void Shuffle(List<int> list)
        {
            for (int i = list.Count - 1; i > 0; i--)
            {
                int j = random.Next(i + 1);
                int swap = list[i]; list[i] = list[j]; list[j] = swap;
            }
        }
    }
}
