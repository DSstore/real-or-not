using System.IO;
using MotionPlay.Unity;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MotionPlay.Editor
{
    /// <summary>
    /// Keeps the player's copy of the question bank in step with the repository's data/questions.json, which is the
    /// one source of truth. The Editor reads the repository file in place; a built player reads
    /// Assets/StreamingAssets/questions.json (git-ignored, refreshed before every build and when the scene is made).
    /// </summary>
    public sealed class QuestionBankBuildStep : IPreprocessBuildWithReport
    {
        public int callbackOrder => 0;

        public void OnPreprocessBuild(BuildReport report) => CopyBank();

        public static bool CopyBank()
        {
            string source = Path.GetFullPath(Path.Combine(Application.dataPath, "../../..", "data", QuestionBankLoader.FileName));
            if (!File.Exists(source))
            {
                Debug.LogWarning("MotionPlay: " + source + " was not found, so the player will have no question bank.");
                return false;
            }
            Directory.CreateDirectory(Application.streamingAssetsPath);
            string target = Path.Combine(Application.streamingAssetsPath, QuestionBankLoader.FileName);
            File.Copy(source, target, true);
            AssetDatabase.ImportAsset("Assets/StreamingAssets/" + QuestionBankLoader.FileName);
            return true;
        }
    }

    public static class ScamQuizSceneSetup
    {
        public const string ScenePath = "Assets/MotionPlay/Scenes/ScamQuiz.unity";

        [MenuItem("MotionPlay/Create Scam Quiz Scene")]
        public static void CreateScene()
        {
            if (Application.isPlaying || !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            if (File.Exists(ScenePath) && !EditorUtility.DisplayDialog("MotionPlay", "Replace the existing Scam Quiz scene?", "Replace", "Cancel")) return;
            Build();
            Debug.Log("MotionPlay Scam Quiz scene created. Press Play, then start Python tracking with TRACKING_PLAYERS=2.");
        }

        /// <summary>Unattended version for batch mode: <c>Unity -batchmode -executeMethod MotionPlay.Editor.ScamQuizSceneSetup.CreateSceneBatch</c>.</summary>
        public static void CreateSceneBatch() => Build();

        private static void Build()
        {
            Directory.CreateDirectory("Assets/MotionPlay/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var cameraObject = new GameObject("Main Camera") { tag = "MainCamera" };
            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color32(232, 237, 245, 255); // light, so dark text on white panels has strong contrast
            camera.orthographic = true;
            camera.orthographicSize = 3;
            cameraObject.transform.position = new Vector3(0, 0, -10);

            var receiverObject = new GameObject("MotionPlay Receiver");
            var receiver = receiverObject.AddComponent<UdpReceiver>();
            receiverObject.AddComponent<ReceiverDebugPanel>().SetExpanded(false);

            var cursorControllers = new HandCursorController[2];
            for (int player = 0; player < 2; player++)
            {
                var cursorObject = new GameObject("Hand Cursor " + (player + 1));
                var renderer = cursorObject.AddComponent<SpriteRenderer>();
                renderer.enabled = false;
                renderer.sortingOrder = 100;
                cursorObject.AddComponent<CursorDisc>().Configure(
                    ScamQuizController.PlayerColors[player], ScamQuizController.PlayerRims[player],
                    player == 0 ? CursorShape.Disc : CursorShape.Diamond);
                var cursor = cursorObject.AddComponent<HandCursorController>();
                cursor.Configure(receiver, camera, player);
                // Larger than the garden's cursor: easier to see and to hold on an answer from arm's length.
                var serialized = new SerializedObject(cursor);
                serialized.FindProperty("cursorRadius").floatValue = 0.2f;
                serialized.ApplyModifiedPropertiesWithoutUndo();
                cursorControllers[player] = cursor;
            }

            var quizObject = new GameObject("Scam Quiz");
            quizObject.AddComponent<ScamQuizController>().Configure(cursorControllers[0], cursorControllers[1], camera, 2);

            QuestionBankBuildStep.CopyBank();
            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.Refresh();
            Selection.activeGameObject = quizObject;
        }
    }
}
