using System;
using System.IO;
using System.Linq;
using System.Text;
using MotionPlay.Games;
using NUnit.Framework;

namespace MotionPlay.Tests
{
    /// <summary>Builds small synthetic question banks, so the tests do not depend on the real question file.</summary>
    public static class QuizFixture
    {
        /// <summary>
        /// A bank with <paramref name="perDifficulty"/> questions at each of difficulties 1 to 5, spread over
        /// <paramref name="categories"/> categories. The right answer sits at a different position in each question.
        /// </summary>
        public static string BankJson(int perDifficulty = 4, int categories = 5, int extraExplanationCharacters = 0,
            int finalQuestions = 0)
        {
            var text = new StringBuilder("{\"version\":1,\"questions\":[");
            int number = 0;
            for (int difficulty = 1; difficulty <= 5; difficulty++)
                for (int n = 0; n < perDifficulty; n++, number++)
                {
                    string id = "q" + difficulty + "-" + n;
                    int correct = number % 4;
                    var choices = new string[4];
                    for (int c = 0; c < 4; c++) choices[c] = c == correct ? "right " + id : "wrong " + c + " " + id;
                    if (number > 0) text.Append(',');
                    text.Append("{\"id\":\"" + id + "\",\"category\":\"cat" + (number % categories) + "\",\"difficulty\":" + difficulty +
                                ",\"question\":\"Question " + id + "?\",\"choices\":[\"" + string.Join("\",\"", choices) +
                                "\"],\"correct\":" + correct + ",\"explanation\":\"Because " + id + "." + new string('x', extraExplanationCharacters) + "\"," +
                                "\"source\":\"https://www.digitalforlife.gov.sg/page-" + id + "\"}");
                }
            // The final-question pool: very hard everyday-skills questions that are only ever asked last.
            for (int n = 0; n < finalQuestions; n++, number++)
            {
                string id = "final-" + n;
                int correct = number % 4;
                var choices = new string[4];
                for (int c = 0; c < 4; c++) choices[c] = c == correct ? "right " + id : "wrong " + c + " " + id;
                if (number > 0) text.Append(',');
                text.Append("{\"id\":\"" + id + "\",\"category\":\"" + QuestionBank.FinalCategory + "\",\"difficulty\":5" +
                            ",\"question\":\"Question " + id + "?\",\"choices\":[\"" + string.Join("\",\"", choices) +
                            "\"],\"correct\":" + correct + ",\"explanation\":\"Because " + id + ".\"," +
                            "\"source\":\"https://www.digitalforlife.gov.sg/page-" + id + "\"}");
            }
            return text.Append("]}").ToString();
        }

        public static QuestionBank Bank(int perDifficulty = 4, int categories = 5, int extraExplanationCharacters = 0,
            int finalQuestions = 0) =>
            QuestionBank.Parse(BankJson(perDifficulty, categories, extraExplanationCharacters, finalQuestions));

        /// <summary>The real question file, found by walking up from the working folder; null if it is not there.</summary>
        public static string FindShippedBank()
        {
            foreach (string start in new[] { Directory.GetCurrentDirectory(), AppContext.BaseDirectory })
                for (var folder = new DirectoryInfo(start); folder != null; folder = folder.Parent)
                {
                    string candidate = Path.Combine(folder.FullName, "data", "questions.json");
                    if (File.Exists(candidate)) return candidate;
                }
            return null;
        }
    }

    public sealed class QuestionBankTests
    {
        [Test]
        public void AValidBankIsParsed()
        {
            QuestionBank bank = QuizFixture.Bank(perDifficulty: 2);
            Assert.AreEqual(1, bank.Version);
            Assert.AreEqual(10, bank.Questions.Count);
            QuizQuestion first = bank.Questions[0];
            Assert.AreEqual("q1-0", first.Id);
            Assert.AreEqual(1, first.Difficulty);
            Assert.AreEqual(4, first.Choices.Count);
            Assert.AreEqual("right q1-0", first.Choices[first.Correct]);
            Assert.AreEqual("Because q1-0.", first.Explanation);
            Assert.AreEqual(2, bank.CountAtOrBelow(1));
            Assert.AreEqual(10, bank.CountAtOrBelow(5));
        }

        [Test]
        public void EveryProblemIsReportedNotJustTheFirst()
        {
            const string bad = "{\"version\":1,\"questions\":[" +
                "{\"id\":\"a\",\"category\":\"c\",\"difficulty\":9,\"question\":\"q\",\"choices\":[\"1\",\"2\",\"3\"],\"correct\":7,\"explanation\":\"e\",\"source\":\"s\"}," +
                "{\"id\":\"b\",\"category\":\"c\",\"difficulty\":1,\"question\":\"q\",\"choices\":[\"1\",\"2\",\"3\",\"4\"],\"correct\":0,\"explanation\":\"e\",\"source\":\"s\"}," +
                "{\"id\":\"b\",\"category\":\"c\",\"difficulty\":1,\"question\":\"q\",\"choices\":[\"1\",\"2\",\"3\",\"4\"],\"correct\":0,\"explanation\":\"e\",\"source\":\"s\"}]}";
            var error = Assert.Throws<QuestionBankException>(() => QuestionBank.Parse(bad));
            Assert.IsTrue(error.Problems.Any(p => p.Contains("difficulty")), error.Message);
            Assert.IsTrue(error.Problems.Any(p => p.Contains("choices")), error.Message);
            Assert.IsTrue(error.Problems.Any(p => p.Contains("correct")), error.Message);
            Assert.IsTrue(error.Problems.Any(p => p.Contains("Duplicate question id 'b'")), error.Message);
        }

        [TestCase("")]
        [TestCase("{not json")]
        [TestCase("[]")]
        [TestCase("{\"version\":1}")]
        [TestCase("{\"version\":1,\"questions\":[]}")]
        [TestCase("{\"version\":0,\"questions\":[{}]}")]
        public void UnusableFilesAreRefused(string json)
        {
            Assert.Throws<QuestionBankException>(() => QuestionBank.Parse(json));
        }

        [Test]
        public void ChoicesMustBeDifferentAndNotBlank()
        {
            string template = "{{\"version\":1,\"questions\":[{{\"id\":\"a\",\"category\":\"c\",\"difficulty\":1,\"question\":\"q\"," +
                "\"choices\":[{0}],\"correct\":0,\"explanation\":\"e\",\"source\":\"s\"}}]}}";
            Assert.Throws<QuestionBankException>(() => QuestionBank.Parse(string.Format(template, "\"Same\",\"same \",\"c\",\"d\"")));
            Assert.Throws<QuestionBankException>(() => QuestionBank.Parse(string.Format(template, "\"a\",\"\",\"c\",\"d\"")));
            Assert.DoesNotThrow(() => QuestionBank.Parse(string.Format(template, "\"a\",\"b\",\"c\",\"d\"")));
        }

        [Test]
        public void UnknownFieldsAreIgnoredSoANewerBankStillLoads()
        {
            string json = QuizFixture.BankJson(1).Replace("\"version\":1", "\"version\":1,\"note\":\"x\"")
                .Replace("\"difficulty\":1", "\"difficulty\":1,\"colour\":\"red\"");
            Assert.AreEqual(5, QuestionBank.Parse(json).Questions.Count);
        }

        [Test]
        public void TheShippedQuestionFileLoadsAndMeetsTheCoverageRule()
        {
            string path = QuizFixture.FindShippedBank();
            if (path == null) Assert.Ignore("data/questions.json is not reachable from here.");
            QuestionBank bank = QuestionBank.Parse(File.ReadAllText(path));
            Assert.GreaterOrEqual(bank.Questions.Count, 50);
            for (int level = 1; level <= 5; level++)
            {
                int atLevel = bank.Questions.Count(q => q.Difficulty == level && q.Category != QuestionBank.FinalCategory);
                Assert.GreaterOrEqual(atLevel, 10, "difficulty " + level + " needs at least 10 ordinary questions");
            }
            Assert.GreaterOrEqual(bank.FinalCount, 20, "the last-question pool");
            Assert.AreEqual(bank.Questions.Count, bank.Questions.Select(q => q.Id).Distinct().Count());
            // The easiest level must be able to fill a round, with some left over so rounds do not repeat at once.
            Assert.GreaterOrEqual(bank.CountAtOrBelow(1), 10);
        }
    }

    public sealed class QuizPickerTests
    {
        private static readonly QuestionBank Big = QuizFixture.Bank(perDifficulty: 6, categories: 7);

        [Test]
        public void PicksFiveDifferentQuestionsWithinTheLevel()
        {
            for (int level = 1; level <= 5; level++)
            {
                var round = QuizPicker.Pick(Big, level, 5, null, new Random(level));
                Assert.AreEqual(5, round.Count);
                Assert.AreEqual(5, round.Select(q => q.Id).Distinct().Count());
                Assert.IsTrue(round.All(q => q.Difficulty <= level), "level " + level);
            }
        }

        [Test]
        public void LevelOneAsksOnlyTheEasiestQuestions()
        {
            Assert.IsTrue(QuizPicker.Pick(Big, 1, 5, null, new Random(3)).All(q => q.Difficulty == 1));
        }

        [Test]
        public void QuestionsComeEasiestFirst()
        {
            for (int seed = 0; seed < 20; seed++)
            {
                int[] difficulties = QuizPicker.Pick(Big, 5, 5, null, new Random(seed)).Select(q => q.Difficulty).ToArray();
                CollectionAssert.AreEqual(difficulties.OrderBy(d => d).ToArray(), difficulties);
            }
        }

        [Test]
        public void CategoriesAreSpreadBeforeAnyRepeats()
        {
            for (int seed = 0; seed < 20; seed++)
                Assert.AreEqual(5, QuizPicker.Pick(Big, 5, 5, null, new Random(seed)).Select(q => q.Category).Distinct().Count());
        }

        [Test]
        public void RecentQuestionsAreAvoidedWhileOthersExist()
        {
            var first = QuizPicker.Pick(Big, 5, 5, null, new Random(1));
            var second = QuizPicker.Pick(Big, 5, 5, first.Select(q => q.Id), new Random(1));
            Assert.IsFalse(second.Any(q => first.Any(f => f.Id == q.Id)));
        }

        [Test]
        public void RecentQuestionsAreUsedWhenThePoolIsTooSmall()
        {
            QuestionBank small = QuizFixture.Bank(perDifficulty: 6, categories: 6);
            var all = small.Questions.Where(q => q.Difficulty == 1).Select(q => q.Id).ToList();
            Assert.AreEqual(5, QuizPicker.Pick(small, 1, 5, all, new Random(2)).Count); // six questions, all recent
        }

        [Test]
        public void ASmallPoolGivesAShortRoundNotAnError()
        {
            QuestionBank tiny = QuizFixture.Bank(perDifficulty: 2, categories: 2);
            Assert.AreEqual(2, QuizPicker.Pick(tiny, 1, 5, null, new Random(0)).Count);
        }

        [Test]
        public void TheSameSeedGivesTheSameRound()
        {
            string[] Ids(int seed) => QuizPicker.Pick(Big, 3, 5, null, new Random(seed)).Select(q => q.Id).ToArray();
            CollectionAssert.AreEqual(Ids(7), Ids(7));
            Assert.IsTrue(Enumerable.Range(0, 10).Select(s => string.Join(",", Ids(s))).Distinct().Count() > 1);
        }

        [Test]
        public void EveryQuestionCanComeUp()
        {
            var seen = new System.Collections.Generic.HashSet<string>();
            var random = new Random(11);
            for (int i = 0; i < 400; i++)
                foreach (QuizQuestion q in QuizPicker.Pick(Big, 5, 5, null, random)) seen.Add(q.Id);
            Assert.AreEqual(Big.Questions.Count, seen.Count);
        }

        private static readonly QuestionBank WithFinals = QuizFixture.Bank(perDifficulty: 6, categories: 7, finalQuestions: 6);

        [Test]
        public void TheLastQuestionIsFromTheFinalPoolAndNoOtherIs()
        {
            for (int level = 1; level <= 5; level++)
                for (int seed = 0; seed < 15; seed++)
                {
                    var round = QuizPicker.Pick(WithFinals, level, 5, null, new Random(seed * 7 + level));
                    Assert.AreEqual(5, round.Count, "level " + level);
                    Assert.AreEqual(QuestionBank.FinalCategory, round[4].Category);
                    Assert.IsTrue(round.Take(4).All(q => q.Category != QuestionBank.FinalCategory));
                    Assert.IsTrue(round.Take(4).All(q => q.Difficulty <= level), "the others still follow the level");
                }
        }

        [Test]
        public void TheFinalQuestionIgnoresTheLevelButTheOthersDoNot()
        {
            var round = QuizPicker.Pick(WithFinals, 1, 5, null, new Random(4));
            Assert.AreEqual(5, round[4].Difficulty);
            CollectionAssert.AreEqual(new[] { 1, 1, 1, 1 }, round.Take(4).Select(q => q.Difficulty).ToArray());
        }

        [Test]
        public void TheOthersAreEasiestFirstAndSpreadOverTopics()
        {
            for (int seed = 0; seed < 20; seed++)
            {
                var others = QuizPicker.Pick(WithFinals, 5, 5, null, new Random(seed)).Take(4).ToList();
                CollectionAssert.AreEqual(others.Select(q => q.Difficulty).OrderBy(d => d).ToArray(), others.Select(q => q.Difficulty).ToArray());
                Assert.AreEqual(4, others.Select(q => q.Category).Distinct().Count());
            }
        }

        [Test]
        public void TheFinalQuestionAvoidsRecentOnesWhileOthersExist()
        {
            var finals = WithFinals.Questions.Where(q => q.Category == QuestionBank.FinalCategory).Select(q => q.Id).ToList();
            for (int seed = 0; seed < 20; seed++)
                Assert.AreEqual(finals[5], QuizPicker.Pick(WithFinals, 3, 5, finals.Take(5), new Random(seed))[4].Id);
            Assert.AreEqual(QuestionBank.FinalCategory, QuizPicker.Pick(WithFinals, 3, 5, finals, new Random(1))[4].Category);
        }

        [Test]
        public void EveryFinalQuestionCanComeUp()
        {
            var seen = new System.Collections.Generic.HashSet<string>();
            var random = new Random(3);
            for (int i = 0; i < 200; i++) seen.Add(QuizPicker.Pick(WithFinals, 2, 5, null, random)[4].Id);
            Assert.AreEqual(6, seen.Count);
        }

        [Test]
        public void TheFinalPoolDoesNotCountTowardsWhatFillsARound()
        {
            Assert.AreEqual(6, WithFinals.FinalCount);
            Assert.AreEqual(6, WithFinals.CountAtOrBelow(1));
            Assert.AreEqual(30, WithFinals.CountAtOrBelow(5));
            Assert.AreEqual(0, Big.FinalCount);
        }

        [Test]
        public void ARoundOfOneIsJustTheFinalQuestion()
        {
            var round = QuizPicker.Pick(WithFinals, 3, 1, null, new Random(2));
            Assert.AreEqual(1, round.Count);
            Assert.AreEqual(QuestionBank.FinalCategory, round[0].Category);
        }

        [Test]
        public void TheShippedBankEndsEveryRoundWithAFinalQuestion()
        {
            string path = QuizFixture.FindShippedBank();
            if (path == null) Assert.Ignore("data/questions.json is not reachable from here.");
            QuestionBank bank = QuestionBank.Parse(System.IO.File.ReadAllText(path));
            for (int level = 1; level <= 5; level++)
            {
                var round = QuizPicker.Pick(bank, level, 5, null, new Random(level));
                Assert.AreEqual(5, round.Count);
                Assert.AreEqual(QuestionBank.FinalCategory, round[4].Category);
            }
        }

        [Test]
        public void BadArgumentsAreRefused()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => QuizPicker.Pick(Big, 0, 5, null, new Random(1)));
            Assert.Throws<ArgumentOutOfRangeException>(() => QuizPicker.Pick(Big, 6, 5, null, new Random(1)));
            Assert.Throws<ArgumentOutOfRangeException>(() => QuizPicker.Pick(Big, 3, 0, null, new Random(1)));
            Assert.Throws<ArgumentOutOfRangeException>(() => QuizPicker.Pick(Big, 3, 21, null, new Random(1)));
            Assert.Throws<ArgumentNullException>(() => QuizPicker.Pick(null, 3, 5, null, new Random(1)));
            Assert.Throws<ArgumentNullException>(() => QuizPicker.Pick(Big, 3, 5, null, null));
        }
    }

    public sealed class RecentQuestionListTests
    {
        [Test]
        public void KeepsTheNewestIdsAndMovesARepeatToTheEnd()
        {
            var list = new RecentQuestionList(4);
            list.Add(new[] { "a", "b", "c" });
            list.Add(new[] { "b", "d", "e" });
            CollectionAssert.AreEqual(new[] { "c", "b", "d", "e" }, list.Ids.ToArray());
        }

        [Test]
        public void RoundTripsThroughASavedString()
        {
            var list = new RecentQuestionList();
            list.Add(new[] { "sms-01", "phone-02" });
            RecentQuestionList copy = RecentQuestionList.Parse(list.Serialize());
            CollectionAssert.AreEqual(list.Ids.ToArray(), copy.Ids.ToArray());
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("  ")]
        [TestCase(",,")]
        public void NothingUsableMeansNothingIsRecent(string saved)
        {
            Assert.AreEqual(0, RecentQuestionList.Parse(saved).Ids.Count);
        }

        [Test]
        public void ACapacityBelowOneIsRefused()
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => new RecentQuestionList(0));
        }
    }
}
