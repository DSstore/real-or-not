using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MotionPlay.Games
{
    /// <summary>One quiz question. Choices are always the four written in the bank; the game may show fewer.</summary>
    public sealed class QuizQuestion
    {
        public string Id { get; }
        public string Category { get; }
        /// <summary>1 = an obvious scam or an easy fact, 5 = subtle or realistic.</summary>
        public int Difficulty { get; }
        public string Text { get; }
        public IReadOnlyList<string> Choices { get; }
        /// <summary>Zero-based index into <see cref="Choices"/> of the right answer.</summary>
        public int Correct { get; }
        public string Explanation { get; }
        public string Source { get; }

        internal QuizQuestion(string id, string category, int difficulty, string text, string[] choices, int correct,
            string explanation, string source)
        {
            Id = id; Category = category; Difficulty = difficulty; Text = text;
            Choices = Array.AsReadOnly(choices); Correct = correct; Explanation = explanation; Source = source;
        }
    }

    /// <summary>The question file is unreadable or invalid; <see cref="Problems"/> lists everything found wrong.</summary>
    public sealed class QuestionBankException : Exception
    {
        public IReadOnlyList<string> Problems { get; }

        public QuestionBankException(IEnumerable<string> problems) : base(string.Join("; ", problems))
        {
            Problems = problems.ToList().AsReadOnly();
        }
    }

    /// <summary>
    /// The question bank (data/questions.json), loaded and checked like the Python loader in shared/questions.py:
    /// the same fields and limits, every problem reported at once. Unlike the Python tool this reader ignores
    /// unknown fields and does not require a Digital for Life source address, so a newer bank still loads.
    /// </summary>
    public sealed class QuestionBank
    {
        public const int ChoiceCount = 4;
        public const int MinDifficulty = 1;
        public const int MaxDifficulty = 5;
        /// <summary>
        /// Questions about everyday digital skills rather than scams. The last question of a round is one of these,
        /// whatever the level, and no other question in a round is (see <see cref="QuizPicker"/>).
        /// </summary>
        public const string FinalCategory = "digital_skills";

        public int Version { get; }
        public IReadOnlyList<QuizQuestion> Questions { get; }

        private QuestionBank(int version, List<QuizQuestion> questions)
        {
            Version = version; Questions = questions.AsReadOnly();
        }

        /// <summary>
        /// Questions that can fill a round at this level: difficulty at most <paramref name="difficulty"/>, not counting the
        /// final-question pool, which is only ever asked last.
        /// </summary>
        public int CountAtOrBelow(int difficulty) =>
            Questions.Count(question => question.Difficulty <= difficulty && question.Category != FinalCategory);

        /// <summary>How many questions can be asked last.</summary>
        public int FinalCount => Questions.Count(question => question.Category == FinalCategory);

        public static QuestionBank Parse(string json)
        {
            var problems = new List<string>();
            JObject root;
            try { root = JObject.Parse(json ?? ""); }
            catch (JsonReaderException error) { throw new QuestionBankException(new[] { "The question file is not valid JSON: " + error.Message }); }

            JToken version = root["version"];
            if (version == null || version.Type != JTokenType.Integer || version.Value<long>() < 1)
                problems.Add("version must be a whole number of at least 1.");
            var items = root["questions"] as JArray;
            if (items == null || items.Count == 0)
            {
                problems.Add("questions must be a non-empty list.");
                throw new QuestionBankException(problems);
            }

            var parsed = new List<QuizQuestion>();
            var seen = new HashSet<string>();
            for (int position = 0; position < items.Count; position++)
            {
                QuizQuestion question = ParseQuestion(items[position], position + 1, problems);
                if (question == null) continue;
                if (!seen.Add(question.Id)) problems.Add("Duplicate question id '" + question.Id + "'.");
                parsed.Add(question);
            }
            if (problems.Count > 0) throw new QuestionBankException(problems);
            return new QuestionBank((int)version.Value<long>(), parsed);
        }

        private static QuizQuestion ParseQuestion(JToken token, int position, List<string> problems)
        {
            var item = token as JObject;
            string name = "question " + position;
            if (item == null) { problems.Add(name + " must be an object."); return null; }
            string id = Text(item, "id", 64);
            if (id != null) name += " (" + id + ")";
            int before = problems.Count;

            if (id == null) problems.Add(name + ": id must be non-empty text of at most 64 characters.");
            string category = Text(item, "category", 64);
            if (category == null) problems.Add(name + ": category must be non-empty text.");
            int? difficulty = Whole(item, "difficulty");
            if (difficulty == null || difficulty < MinDifficulty || difficulty > MaxDifficulty)
                problems.Add(name + ": difficulty must be a whole number from " + MinDifficulty + " to " + MaxDifficulty + ".");
            string text = Text(item, "question", 400);
            if (text == null) problems.Add(name + ": question must be non-empty text of at most 400 characters.");
            string explanation = Text(item, "explanation", 500);
            if (explanation == null) problems.Add(name + ": explanation must be non-empty text of at most 500 characters.");
            string source = Text(item, "source", 300);
            if (source == null) problems.Add(name + ": source must be non-empty text.");

            string[] choices = null;
            var array = item["choices"] as JArray;
            if (array != null && array.Count == ChoiceCount)
            {
                choices = array.Select(choice => choice.Type == JTokenType.String ? choice.Value<string>() : null).ToArray();
                if (choices.Any(choice => string.IsNullOrWhiteSpace(choice) || choice.Length > 120)) choices = null;
            }
            if (choices == null)
                problems.Add(name + ": choices must be a list of exactly " + ChoiceCount + " non-empty texts of at most 120 characters.");
            else if (choices.Select(choice => choice.Trim().ToLowerInvariant()).Distinct().Count() != ChoiceCount)
                problems.Add(name + ": choices must all be different.");
            int? correct = Whole(item, "correct");
            if (correct == null || correct < 0 || correct >= ChoiceCount)
                problems.Add(name + ": correct must be a whole number from 0 to " + (ChoiceCount - 1) + ".");

            if (problems.Count > before) return null;
            return new QuizQuestion(id, category, difficulty.Value, text.Trim(), choices, correct.Value,
                explanation.Trim(), source);
        }

        private static string Text(JObject item, string field, int limit)
        {
            JToken token = item[field];
            if (token == null || token.Type != JTokenType.String) return null;
            string value = token.Value<string>();
            return string.IsNullOrWhiteSpace(value) || value.Length > limit ? null : value;
        }

        private static int? Whole(JObject item, string field)
        {
            JToken token = item[field];
            if (token == null || token.Type != JTokenType.Integer) return null;
            long value = token.Value<long>();
            return value < int.MinValue || value > int.MaxValue ? null : (int?)value;
        }
    }

    /// <summary>
    /// Chooses the questions for one round, with the same rules as pick_round in shared/questions.py: only questions
    /// up to the level's difficulty, none twice, a new category where possible, questions asked recently last, and
    /// the easiest first. If the bank has a final-question pool, the last question is one of those whatever the level
    /// (preferring one not asked recently) and the others come from the rest of the bank. Fewer than the requested
    /// count come back only if fewer exist.
    /// </summary>
    public static class QuizPicker
    {
        public const int MaxRoundLength = 20;

        public static IReadOnlyList<QuizQuestion> Pick(QuestionBank bank, int level, int count,
            IEnumerable<string> recent, Random random)
        {
            if (bank == null) throw new ArgumentNullException(nameof(bank));
            if (random == null) throw new ArgumentNullException(nameof(random));
            if (level < QuestionBank.MinDifficulty || level > QuestionBank.MaxDifficulty)
                throw new ArgumentOutOfRangeException(nameof(level));
            if (count < 1 || count > MaxRoundLength) throw new ArgumentOutOfRangeException(nameof(count));

            var recentIds = new HashSet<string>(recent ?? Enumerable.Empty<string>());
            List<QuizQuestion> finals = bank.Questions.Where(question => question.Category == QuestionBank.FinalCategory).ToList();
            int regularCount = finals.Count > 0 ? count - 1 : count;
            var remaining = bank.Questions
                .Where(question => question.Difficulty <= level && question.Category != QuestionBank.FinalCategory).ToList();
            var chosen = new List<QuizQuestion>();
            var usedCategories = new HashSet<string>();
            while (remaining.Count > 0 && chosen.Count < regularCount)
            {
                List<QuizQuestion> fresh = remaining.Where(question => !recentIds.Contains(question.Id)).ToList();
                List<QuizQuestion> pool = fresh.Where(question => !usedCategories.Contains(question.Category)).ToList();
                if (pool.Count == 0) pool = fresh;
                if (pool.Count == 0) pool = remaining.Where(question => !usedCategories.Contains(question.Category)).ToList();
                if (pool.Count == 0) pool = remaining;
                QuizQuestion pick = pool[random.Next(pool.Count)];
                chosen.Add(pick);
                usedCategories.Add(pick.Category);
                remaining.Remove(pick);
            }
            // OrderBy is stable, so questions of equal difficulty keep their random order.
            List<QuizQuestion> ordered = chosen.OrderBy(question => question.Difficulty).ToList();
            if (finals.Count > 0)
            {
                List<QuizQuestion> freshFinals = finals.Where(question => !recentIds.Contains(question.Id)).ToList();
                if (freshFinals.Count == 0) freshFinals = finals;
                ordered.Add(freshFinals[random.Next(freshFinals.Count)]);
            }
            return ordered.AsReadOnly();
        }
    }

    /// <summary>
    /// The ids of the questions asked in the last few rounds, so a family playing again does not see the same ones.
    /// Saved as one comma-separated string (ids are lowercase letters, digits, '-' and '_').
    /// </summary>
    public sealed class RecentQuestionList
    {
        public const int DefaultCapacity = 15; // three rounds of five

        private readonly int capacity;
        private readonly List<string> ids = new List<string>();

        public RecentQuestionList(int capacity = DefaultCapacity)
        {
            if (capacity < 1) throw new ArgumentOutOfRangeException(nameof(capacity));
            this.capacity = capacity;
        }

        /// <summary>Oldest first.</summary>
        public IReadOnlyList<string> Ids => ids.AsReadOnly();

        /// <summary>Record a finished round. An id asked again moves to the end; only the newest ones are kept.</summary>
        public void Add(IEnumerable<string> asked)
        {
            if (asked == null) throw new ArgumentNullException(nameof(asked));
            foreach (string id in asked)
            {
                if (string.IsNullOrWhiteSpace(id)) continue;
                ids.Remove(id);
                ids.Add(id);
            }
            if (ids.Count > capacity) ids.RemoveRange(0, ids.Count - capacity);
        }

        public string Serialize() => string.Join(",", ids);

        /// <summary>Read a saved list; anything unreadable just means nothing is recent.</summary>
        public static RecentQuestionList Parse(string saved, int capacity = DefaultCapacity)
        {
            var list = new RecentQuestionList(capacity);
            if (!string.IsNullOrWhiteSpace(saved)) list.Add(saved.Split(',').Select(part => part.Trim()));
            return list;
        }
    }
}
