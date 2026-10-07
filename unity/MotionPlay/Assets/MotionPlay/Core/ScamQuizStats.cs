using System;
using System.Collections.Generic;

namespace MotionPlay.Games
{
    /// <summary>One question's outcome for one player, in the form that will be sent after a round.</summary>
    public sealed class QuizResponse
    {
        public string QuestionId { get; }
        /// <summary>The choice picked, as an index into the question's written choices (0 to 3), or -1 if time ran out.</summary>
        public int Selected { get; }
        /// <summary>Tracked-time milliseconds from the question appearing to the answer; null if time ran out.</summary>
        public int? ResponseMs { get; }
        public bool IsCorrect { get; }

        internal QuizResponse(string questionId, int selected, int? responseMs, bool isCorrect)
        {
            QuestionId = questionId; Selected = selected; ResponseMs = responseMs; IsCorrect = isCorrect;
        }
    }

    /// <summary>
    /// One player's statistics for one round of the scam quiz. Times are seconds of tracked time (the question clock
    /// stops while nobody's hand is tracked). A metric with no samples is null, never a made-up zero. Wrong answers
    /// are not penalised: they are counted so that the player and the reports can see what to practise.
    /// </summary>
    public sealed class ScamQuizStats
    {
        private readonly List<QuizResponse> responses = new List<QuizResponse>();
        private double responseSum, fastest, slowest;

        /// <summary>Questions presented to this player this round.</summary>
        public int Total { get; private set; }
        public int Correct { get; private set; }
        public int Wrong { get; private set; }
        /// <summary>Questions where time ran out before the player answered.</summary>
        public int Skipped { get; private set; }
        public int Answered => Correct + Wrong;
        public int CurrentStreak { get; private set; }
        public int BestStreak { get; private set; }
        public IReadOnlyList<QuizResponse> Responses => responses.AsReadOnly();

        /// <summary>Correct answers divided by questions presented (a skipped question is not correct), 0 to 1.</summary>
        public double? Score => Total > 0 ? (double?)((double)Correct / Total) : null;
        public double? AverageResponseTime => Answered > 0 ? (double?)(responseSum / Answered) : null;
        public double? FastestResponse => Answered > 0 ? (double?)fastest : null;
        public double? SlowestResponse => Answered > 0 ? (double?)slowest : null;
        /// <summary>Sum of response times, so a group can be averaged properly.</summary>
        public double ResponseTimeSum => responseSum;

        internal void Reset()
        {
            responses.Clear();
            Total = Correct = Wrong = Skipped = CurrentStreak = BestStreak = 0;
            responseSum = fastest = slowest = 0;
        }

        internal void RecordAnswer(QuizQuestion question, int selected, double seconds)
        {
            if (question == null) throw new ArgumentNullException(nameof(question));
            if (selected < 0 || selected >= question.Choices.Count) throw new ArgumentOutOfRangeException(nameof(selected));
            if (double.IsNaN(seconds) || double.IsInfinity(seconds) || seconds < 0) throw new ArgumentOutOfRangeException(nameof(seconds));
            bool correct = selected == question.Correct;
            Total++;
            if (correct) { Correct++; CurrentStreak++; BestStreak = Math.Max(BestStreak, CurrentStreak); }
            else { Wrong++; CurrentStreak = 0; }
            fastest = Answered == 1 ? seconds : Math.Min(fastest, seconds);
            slowest = Answered == 1 ? seconds : Math.Max(slowest, seconds);
            responseSum += seconds;
            responses.Add(new QuizResponse(question.Id, selected, (int)Math.Round(seconds * 1000), correct));
        }

        internal void RecordSkip(QuizQuestion question)
        {
            if (question == null) throw new ArgumentNullException(nameof(question));
            Total++;
            Skipped++;
            CurrentStreak = 0;
            responses.Add(new QuizResponse(question.Id, -1, null, false));
        }
    }
}
