using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace MotionPlay.Games
{
    /// <summary>
    /// Adaptive difficulty for the scam quiz, shared by the players of a round. Five levels map to fixed presets
    /// (time per question, how many choices are shown, how hard the questions may be, how long to hold a choice).
    /// The quiz starts at level 1 and moves one step at a time, and only after two qualifying rounds in a row, so a
    /// single lucky or unlucky round does not move it. A round is judged on the group's combined score and speed.
    /// Contains no engine types so the standalone harness tests it. These are gameplay-tuning heuristics for a
    /// portfolio project, not an assessment of anyone's ability.
    /// </summary>
    public sealed class ScamQuizDifficulty
    {
        public const int MinLevel = 1;
        public const int MaxLevel = 5;
        /// <summary>The quiz starts easy and rises slowly.</summary>
        public const int DefaultLevel = 1;

        /// <summary>A combined score at or above this, with quick answers, counts as a round that was too easy.</summary>
        public const double HarderScore = 0.9;
        /// <summary>"Quick" means an average response within this share of the time allowed.</summary>
        public const double HarderSpeedShare = 0.5;
        /// <summary>A combined score at or below this counts as a round that was too hard.</summary>
        public const double EasierScore = 0.4;
        /// <summary>Qualifying rounds in a row required before the level moves.</summary>
        public const int RoundsToChange = 2;
        /// <summary>Rounds with fewer answers than this (all players together) are ignored.</summary>
        public const int MinAnswered = 4;

        private int easyStreak;
        private int hardStreak;

        public ScamQuizDifficulty(int level = DefaultLevel)
        {
            Level = Clamp(level);
        }

        public int Level { get; private set; }
        /// <summary>Label sent with each result (within the protocol's 32-character limit), e.g. <c>level_2</c>.</summary>
        public string Label => LabelFor(Level);

        public static string LabelFor(int level) => "level_" + Clamp(level);

        /// <summary>Parse a label such as <c>level_4</c>; anything else gives null.</summary>
        public static int? ParseLabel(string label)
        {
            const string prefix = "level_";
            if (label == null || !label.StartsWith(prefix, StringComparison.Ordinal)) return null;
            int level;
            if (!int.TryParse(label.Substring(prefix.Length), NumberStyles.None, CultureInfo.InvariantCulture, out level))
                return null;
            return level >= MinLevel && level <= MaxLevel ? (int?)level : null;
        }

        /// <summary>
        /// Rules for a level. The round length, number of players and explanation time are not part of difficulty and
        /// are passed through.
        /// </summary>
        public static ScamQuizSettings SettingsFor(int level, int players = 2, int roundLength = 5,
            double explanationSeconds = 6)
        {
            level = Clamp(level);
            var settings = new ScamQuizSettings
            {
                Players = players, RoundLength = roundLength, ExplanationSeconds = explanationSeconds,
                Level = level, MaxQuestionDifficulty = level
            };
            switch (level)
            {
                case 1: settings.QuestionSeconds = 25; settings.ChoiceCount = 2; settings.DwellSeconds = 1.2; break;
                case 2: settings.QuestionSeconds = 20; settings.ChoiceCount = 2; settings.DwellSeconds = 1.0; break;
                case 3: settings.QuestionSeconds = 15; settings.ChoiceCount = 3; settings.DwellSeconds = 0.8; break;
                case 4: settings.QuestionSeconds = 12; settings.ChoiceCount = 4; settings.DwellSeconds = 0.7; break;
                default: settings.QuestionSeconds = 10; settings.ChoiceCount = 4; settings.DwellSeconds = 0.6; break;
            }
            return settings;
        }

        /// <summary>Set the level directly (for example a manual override) and forget any partial streak.</summary>
        public void SetLevel(int level)
        {
            Level = Clamp(level);
            easyStreak = 0;
            hardStreak = 0;
        }

        /// <summary>Judge a finished round from every player's statistics and possibly move the level by one step.</summary>
        public DifficultyChange RecordRound(IEnumerable<ScamQuizStats> players)
        {
            if (players == null) throw new ArgumentNullException(nameof(players));
            List<ScamQuizStats> group = players.Where(stats => stats != null && stats.Total > 0).ToList();
            int total = group.Sum(stats => stats.Total);
            int answered = group.Sum(stats => stats.Answered);
            if (answered < MinAnswered) return DifficultyChange.None;

            double score = (double)group.Sum(stats => stats.Correct) / total;
            double averageResponse = group.Sum(stats => stats.ResponseTimeSum) / answered;
            double timeAllowed = SettingsFor(Level).QuestionSeconds;
            bool tooEasy = score >= HarderScore && averageResponse <= timeAllowed * HarderSpeedShare;
            bool tooHard = score <= EasierScore;

            easyStreak = tooEasy ? easyStreak + 1 : 0;
            hardStreak = tooHard ? hardStreak + 1 : 0;

            if (easyStreak >= RoundsToChange)
            {
                easyStreak = 0;
                if (Level < MaxLevel) { Level++; return DifficultyChange.Harder; }
            }
            else if (hardStreak >= RoundsToChange)
            {
                hardStreak = 0;
                if (Level > MinLevel) { Level--; return DifficultyChange.Easier; }
            }
            return DifficultyChange.None;
        }

        private static int Clamp(int level) => Math.Max(MinLevel, Math.Min(MaxLevel, level));
    }
}
