using System;
using System.IO;
using MotionPlay.Games;
using UnityEngine;

namespace MotionPlay.Unity
{
    /// <summary>
    /// Finds and reads the question bank. The repository's <c>data/questions.json</c> is the one source of truth: the
    /// Editor reads it in place, and a build copy in StreamingAssets (refreshed before every build) is used by players.
    /// </summary>
    public static class QuestionBankLoader
    {
        public const string FileName = "questions.json";
        public const string EnvironmentVariable = "MOTIONPLAY_QUESTIONS_FILE";

        /// <summary>Where the question file is, in order: an explicit path, the environment variable, the repository (Editor only), then StreamingAssets.</summary>
        public static string ResolvePath(string explicitPath = "")
        {
            string path = string.IsNullOrWhiteSpace(explicitPath)
                ? Environment.GetEnvironmentVariable(EnvironmentVariable) : explicitPath;
            if (!string.IsNullOrWhiteSpace(path)) return path;
#if UNITY_EDITOR
            // Assets is repo/unity/MotionPlay/Assets; the repository's data folder is three levels above.
            string repository = Path.GetFullPath(Path.Combine(Application.dataPath, "../../..", "data", FileName));
            if (File.Exists(repository)) return repository;
#endif
            return Path.Combine(Application.streamingAssetsPath, FileName);
        }

        /// <summary>Read and check the bank; throws <see cref="QuestionBankException"/>, <see cref="IOException"/> or <see cref="UnauthorizedAccessException"/>.</summary>
        public static QuestionBank Load(string explicitPath = "")
        {
            string path = ResolvePath(explicitPath);
            if (!File.Exists(path)) throw new IOException("The question file was not found at " + path + ".");
            return QuestionBank.Parse(File.ReadAllText(path));
        }
    }
}
