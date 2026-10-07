using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MotionPlay.Games;
using MotionPlay.Networking;
using UnityEngine;

namespace MotionPlay.Unity
{
    /// <summary>
    /// The scam quiz for one or two players. Each player moves their own coloured cursor with a hand and holds it on
    /// an answer to choose it. Runs the engine-independent <see cref="ScamQuizGame"/>; this class only turns cursors
    /// into panel numbers and draws the result. Panels, bars and icons are sprites (so the cursors, at sorting order
    /// 100, always draw on top); the words are drawn with the on-screen GUI at sizes meant to be read from arm's length.
    /// Original geometric placeholders only, no artwork.
    /// </summary>
    [DefaultExecutionOrder(10)] // After the hand cursors, so the quiz reads this frame's positions.
    [DisallowMultipleComponent]
    public sealed class ScamQuizController : MonoBehaviour
    {
        [SerializeField] private HandCursorController[] cursors = new HandCursorController[CvStateSlots];
        [SerializeField] private Camera gameplayCamera;
        [SerializeField, Range(1, 2), Tooltip("People playing at once. Two needs the Python engine started with TRACKING_PLAYERS=2.")]
        private int players = 2;
        [SerializeField, Range(1, 20)] private int roundLength = 5;
        [SerializeField, Min(0), Tooltip("How long the right answer and the explanation stay up.")]
        private float explanationSeconds = 6;
        [Tooltip("Move the level up or down after finished rounds. Time, number of answers and question difficulty come from the level.")]
        [SerializeField] private bool adaptive = true;
        [SerializeField, Tooltip("Optional path of a question file; otherwise data/questions.json (Editor) or StreamingAssets.")]
        private string questionFileOverride = "";
        [SerializeField, Min(0.001f)] private float planeDistance = 10f;

        private const int CvStateSlots = 2;
        private const string LevelPrefsKey = "MotionPlay.ScamQuiz.Level";
        private const string RecentPrefsKey = "MotionPlay.ScamQuiz.Recent";
        private const int MaxPanels = QuestionBank.ChoiceCount;

        /// <summary>Player colours, chosen to stay apart for common kinds of colour blindness (blue and orange).</summary>
        public static readonly Color32[] PlayerColors = { new Color32(0, 114, 178, 255), new Color32(230, 159, 0, 255) };
        /// <summary>Lighter rims for the cursors.</summary>
        public static readonly Color32[] PlayerRims = { new Color32(200, 228, 250, 255), new Color32(255, 238, 196, 255) };

        private static readonly Color Ink = new Color32(24, 26, 32, 255);
        private static readonly Color Outline = new Color32(88, 96, 112, 255);
        private static readonly Color PanelFill = Color.white;
        private static readonly Color PanelDim = new Color32(236, 238, 241, 255);
        private static readonly Color CardFill = new Color32(255, 255, 255, 255);
        private static readonly Color Correct = new Color32(0, 158, 115, 255);
        private static readonly Color CorrectTint = new Color32(208, 240, 228, 255);
        private static readonly Color Wrong = new Color32(213, 94, 0, 255);
        private static readonly Color WrongTint = new Color32(252, 226, 210, 255);
        private static readonly Color TrackColor = new Color32(214, 218, 226, 255);
        private static readonly Color TimerColor = new Color32(0, 114, 178, 255);

        private ScamQuizGame game;
        private QuestionBank bank;
        private ScamQuizDifficulty difficulty;
        private RecentQuestionList recent;
        private string loadError;
        private string levelNotice;
        private QuizPhase lastPhase = QuizPhase.Waiting;
        private ResultSender sender;
        private string roundId, roundLevelLabel;
        private long roundStartedAt;
        private readonly string[] lastHand = new string[CvStateSlots];
        private readonly QuizInput[] inputs = new QuizInput[CvStateSlots];
        private readonly bool[] tracked = new bool[CvStateSlots];
        private bool anyTracked;
        private string viewError, lastViewError;

        // Layout, in camera-plane units: x to the right, y up, origin at the middle of the view.
        private Rect hudRect, cardRect, timerRect, buttonRect;
        private readonly Rect[] panelRects = new Rect[MaxPanels];

        // Visuals.
        private readonly List<Texture2D> textures = new List<Texture2D>();
        private readonly List<Sprite> sprites = new List<Sprite>();
        private Sprite whiteSprite, discSprite, diamondSprite, tickSprite, crossSprite;
        private SpriteRenderer cardBorder, cardFillQuad, timerTrack, timerFill, buttonBorder, buttonFill;
        private readonly SpriteRenderer[] panelBorder = new SpriteRenderer[MaxPanels];
        private readonly SpriteRenderer[] panelFill = new SpriteRenderer[MaxPanels];
        private readonly SpriteRenderer[] panelIcon = new SpriteRenderer[MaxPanels];
        private readonly SpriteRenderer[,] panelBar = new SpriteRenderer[MaxPanels, CvStateSlots];
        private readonly SpriteRenderer[,] panelMarker = new SpriteRenderer[MaxPanels, CvStateSlots];
        private readonly SpriteRenderer[] buttonBar = new SpriteRenderer[CvStateSlots];
        private GUIStyle textStyle;

        public ScamQuizGame Game => game;
        public string LoadError => loadError;

        /// <summary>Wire scene references before Play; serialized so the setup survives scene reloads.</summary>
        public void Configure(HandCursorController firstPlayer, HandCursorController secondPlayer, Camera view, int playerCount = 2)
        {
            cursors = new[] { firstPlayer, secondPlayer };
            gameplayCamera = view;
            players = Mathf.Clamp(playerCount, 1, CvStateSlots);
            viewError = lastViewError = null;
        }

        private void Awake()
        {
            BuildVisuals();
            sender = GetComponent<ResultSender>();
            if (sender == null) sender = gameObject.AddComponent<ResultSender>();
            try
            {
                bank = QuestionBankLoader.Load(questionFileOverride);
                recent = RecentQuestionList.Parse(PlayerPrefs.GetString(RecentPrefsKey, ""));
                difficulty = new ScamQuizDifficulty(PlayerPrefs.GetInt(LevelPrefsKey, ScamQuizDifficulty.DefaultLevel));
                game = new ScamQuizGame(bank, Settings(), Environment.TickCount, recent.Ids);
                Debug.Log("MotionPlay scam quiz loaded " + bank.Questions.Count + " questions (level " + difficulty.Level + ").", this);
            }
            catch (Exception problem) when (problem is QuestionBankException || problem is IOException ||
                problem is UnauthorizedAccessException || problem is ArgumentException || problem is InvalidOperationException)
            {
                loadError = "The quiz cannot start: " + problem.Message;
                Debug.LogError("MotionPlay: " + loadError, this);
            }
        }

        private ScamQuizSettings Settings() =>
            ScamQuizDifficulty.SettingsFor(difficulty.Level, players, roundLength, explanationSeconds);

        // ------------------------------------------------------------------------------------------------
        // Per frame
        // ------------------------------------------------------------------------------------------------

        private void Update()
        {
            if (game == null) { HideEverything(); return; }
            if (!TryView()) { HideEverything(); return; }
            ComputeLayout();
            HandleKeys();
            ReadCursors();
            game.Step(Time.deltaTime, inputs);
            TrackPhase();
            Render();
        }

        private bool TryView()
        {
            viewError = null;
            if (gameplayCamera == null) viewError = "Assign the camera to the scam quiz.";
            else if (!gameplayCamera.isActiveAndEnabled || !gameplayCamera.orthographic)
                viewError = "The scam quiz requires an enabled orthographic camera.";
            else
                for (int i = 0; i < players; i++)
                    if (cursors == null || i >= cursors.Length || cursors[i] == null)
                        viewError = "Assign a hand cursor for player " + (i + 1) + ".";
            if (viewError != null)
            {
                if (lastViewError != viewError) Debug.LogError("MotionPlay: " + viewError, this);
                lastViewError = viewError;
                return false;
            }
            lastViewError = null;
            return true;
        }

        private void HandleKeys()
        {
            if (Input.GetKeyDown(KeyCode.R) || Input.GetKeyDown(KeyCode.S))
            {
                if (game.Phase == QuizPhase.Waiting) game.StartNow();
                else if (game.Phase == QuizPhase.Complete) game.Restart();
            }
            if (Input.GetKeyDown(KeyCode.LeftBracket)) ChooseLevel(difficulty.Level - 1);
            if (Input.GetKeyDown(KeyCode.RightBracket)) ChooseLevel(difficulty.Level + 1);
        }

        /// <summary>Manual override with [ and ]; ignored mid-round so a question never changes under the players.</summary>
        private void ChooseLevel(int level)
        {
            if (game.Phase != QuizPhase.Waiting && game.Phase != QuizPhase.Complete) return;
            difficulty.SetLevel(level);
            ApplyLevel();
            levelNotice = "Level set manually.";
        }

        private void ApplyLevel()
        {
            game.ApplySettings(Settings());
            try { PlayerPrefs.SetInt(LevelPrefsKey, difficulty.Level); PlayerPrefs.Save(); }
            catch (Exception issue) { Debug.LogWarning("MotionPlay: could not save the quiz level: " + issue.Message, this); }
        }

        private void ReadCursors()
        {
            anyTracked = false;
            for (int i = 0; i < CvStateSlots; i++)
            {
                tracked[i] = false;
                inputs[i] = new QuizInput(false, -1);
                if (i >= players) continue;
                HandCursorController cursor = cursors[i];
                if (!cursor.isActiveAndEnabled || !cursor.IsTracking) continue;
                tracked[i] = true;
                anyTracked = true;
                if (cursor.CurrentHand != null) lastHand[i] = cursor.CurrentHand;
                Vector3 local = gameplayCamera.transform.InverseTransformPoint(cursor.CurrentPosition.Value);
                inputs[i] = new QuizInput(true, HitPanel(local.x, local.y));
            }
        }

        private int HitPanel(float x, float y)
        {
            var point = new Vector2(x, y);
            if (game.Phase == QuizPhase.Asking)
            {
                for (int i = 0; i < game.Panels.Count; i++)
                    if (panelRects[i].Contains(point)) return i;
            }
            else if (game.Phase == QuizPhase.Complete && buttonRect.Contains(point)) return 0; // "Play again"
            return -1;
        }

        /// <summary>When a round ends: remember its questions and let the level adapt, using both players' results.</summary>
        private void TrackPhase()
        {
            QuizPhase phase = game.Phase;
            if (phase == QuizPhase.Asking && (lastPhase == QuizPhase.Waiting || lastPhase == QuizPhase.Complete)) BeginRound();
            if (phase == QuizPhase.Complete && lastPhase != QuizPhase.Complete) AfterRound();
            lastPhase = phase;
        }

        /// <summary>A round has started: one id shared by the players, when it began, and the level it is played at.</summary>
        private void BeginRound()
        {
            roundId = Guid.NewGuid().ToString("D");
            roundStartedAt = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            roundLevelLabel = ScamQuizDifficulty.LabelFor(game.Level); // before the level can adapt to this round
            for (int i = 0; i < lastHand.Length; i++) lastHand[i] = null;
        }

        /// <summary>Send each player's own result, all with this round's id. They are queued and sent one after another.</summary>
        private void SubmitResults()
        {
            if (roundId == null) return;
            long ended = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            for (int i = 0; i < players; i++)
            {
                if (!game.Players[i].Active) continue;
                ScamQuizStats stats = game.Stats(i);
                if (stats.Total == 0) continue;
                // In a two-player game the "hand" is the player's zone, so fall back to it if no hand was ever read.
                string hand = lastHand[i] ?? (i == 0 ? "left" : "right");
                sender.Submit(QuizSessionResult.FromStats(stats, i, hand, roundLevelLabel, Guid.NewGuid().ToString("D"),
                    roundId, bank.Version, roundStartedAt, ended));
            }
        }

        private string DeliveryText()
        {
            if (sender == null || sender.Error != null) return "Results not sent";
            switch (sender.State)
            {
                case DeliveryState.Sending: return "Saving results...";
                case DeliveryState.Confirmed: return sender.Pending == 0 ? "Results saved" : "Saving results...";
                case DeliveryState.Rejected: return "Results rejected";
                case DeliveryState.NotConfirmed: return "Results NOT saved. Is the result receiver running?";
                default: return "Results not sent";
            }
        }

        private void AfterRound()
        {
            SubmitResults();
            recent.Add(game.AskedQuestionIds);
            try { PlayerPrefs.SetString(RecentPrefsKey, recent.Serialize()); PlayerPrefs.Save(); }
            catch (Exception issue) { Debug.LogWarning("MotionPlay: could not save recent questions: " + issue.Message, this); }
            levelNotice = null;
            if (!adaptive) return;
            DifficultyChange change = difficulty.RecordRound(game.ActiveStats());
            if (change == DifficultyChange.None) return;
            ApplyLevel();
            levelNotice = change == DifficultyChange.Harder ? "Level up: the next round is a little harder."
                                                           : "Level down: the next round is a little easier.";
        }

        // ------------------------------------------------------------------------------------------------
        // Layout
        // ------------------------------------------------------------------------------------------------

        private static Rect Box(float left, float bottom, float right, float top) =>
            new Rect(left, bottom, right - left, top - bottom);

        private void ComputeLayout()
        {
            float h = gameplayCamera.orthographicSize;
            float w = h * gameplayCamera.aspect;
            float margin = 0.06f * h;
            float left = -w + margin, right = w - margin;
            hudRect = Box(left, 0.86f * h, right, h - margin * 0.5f);
            timerRect = Box(left, 0.80f * h, right, 0.85f * h);
            cardRect = Box(left, 0.30f * h, right, 0.77f * h);

            float areaTop = 0.24f * h, areaBottom = -h + margin;
            float gap = 0.05f * h;
            int count = Mathf.Max(1, game.Panels.Count);
            if (count <= 3)
            {
                float rowHeight = Mathf.Min(areaTop - areaBottom, 0.75f * h);
                float top = areaTop, bottom = top - rowHeight;
                float width = (right - left - gap * (count - 1)) / count;
                for (int i = 0; i < count; i++)
                    panelRects[i] = Box(left + i * (width + gap), bottom, left + i * (width + gap) + width, top);
            }
            else
            {
                float rowHeight = (areaTop - areaBottom - gap) / 2;
                float width = (right - left - gap) / 2;
                for (int i = 0; i < count && i < MaxPanels; i++)
                {
                    int column = i % 2, row = i / 2;
                    float x0 = left + column * (width + gap);
                    float y1 = areaTop - row * (rowHeight + gap);
                    panelRects[i] = Box(x0, y1 - rowHeight, x0 + width, y1);
                }
            }
            buttonRect = Box(-0.30f * w, -0.80f * h, 0.30f * w, -0.40f * h);
        }

        private const float MarkerSize = 0.34f, MarkerGap = 0.12f, EdgePad = 0.14f;

        /// <summary>Where a player's "I chose this" marker goes: the top right of the panel, player 1 then player 2.</summary>
        private static Rect MarkerRect(Rect panel, int player)
        {
            float x0 = panel.xMax - EdgePad - 2 * MarkerSize - MarkerGap + player * (MarkerSize + MarkerGap);
            return Box(x0, panel.yMax - EdgePad - MarkerSize, x0 + MarkerSize, panel.yMax - EdgePad);
        }

        /// <summary>The tick or cross, just under the markers.</summary>
        private static Rect IconRect(Rect panel)
        {
            float size = Mathf.Min(0.5f, panel.height * 0.28f);
            float top = panel.yMax - EdgePad - MarkerSize - MarkerGap;
            return Box(panel.xMax - EdgePad - size, top - size, panel.xMax - EdgePad, top);
        }

        /// <summary>The part of a panel left for the answer's words: clear of the markers, icon and bars.</summary>
        private static Rect AnswerTextRect(Rect panel) =>
            Box(panel.xMin + 0.25f, panel.yMin + 0.45f, panel.xMax - 2 * MarkerSize - MarkerGap - EdgePad - 0.15f, panel.yMax - 0.15f);

        private Vector3 World(float x, float y) =>
            gameplayCamera.transform.position + gameplayCamera.transform.right * x +
            gameplayCamera.transform.up * y + gameplayCamera.transform.forward * planeDistance;

        // ------------------------------------------------------------------------------------------------
        // Drawing the sprites
        // ------------------------------------------------------------------------------------------------

        private void Place(SpriteRenderer renderer, Rect rect, Color color)
        {
            if (renderer == null) return;
            renderer.enabled = true;
            renderer.color = color;
            renderer.transform.SetPositionAndRotation(World(rect.center.x, rect.center.y), gameplayCamera.transform.rotation);
            renderer.transform.localScale = new Vector3(Mathf.Max(0.0001f, rect.width), Mathf.Max(0.0001f, rect.height), 1f);
        }

        private static void Hide(SpriteRenderer renderer) { if (renderer != null) renderer.enabled = false; }

        private static Rect Grow(Rect rect, float by) => Box(rect.xMin - by, rect.yMin - by, rect.xMax + by, rect.yMax + by);

        private void HideEverything()
        {
            Hide(cardBorder); Hide(cardFillQuad); Hide(timerTrack); Hide(timerFill); Hide(buttonBorder); Hide(buttonFill);
            for (int i = 0; i < MaxPanels; i++) HidePanel(i);
            for (int p = 0; p < CvStateSlots; p++) Hide(buttonBar[p]);
        }

        private void HidePanel(int i)
        {
            Hide(panelBorder[i]); Hide(panelFill[i]); Hide(panelIcon[i]);
            for (int p = 0; p < CvStateSlots; p++) { Hide(panelBar[i, p]); Hide(panelMarker[i, p]); }
        }

        private void Render()
        {
            QuizPhase phase = game.Phase;
            bool asking = phase == QuizPhase.Asking, feedback = phase == QuizPhase.Feedback;
            Place(cardBorder, Grow(cardRect, 0.03f), Outline);
            Place(cardFillQuad, cardRect, CardFill);

            if (asking || feedback)
            {
                float share = asking ? Mathf.Clamp01((float)(game.TimeRemaining / game.QuestionSeconds)) : 0f;
                Place(timerTrack, timerRect, TrackColor);
                if (share > 0.001f)
                {
                    Rect fill = Box(timerRect.xMin, timerRect.yMin, timerRect.xMin + timerRect.width * share, timerRect.yMax);
                    Place(timerFill, fill, game.TimeRemaining <= 5 ? Wrong : TimerColor);
                }
                else Hide(timerFill);
            }
            else { Hide(timerTrack); Hide(timerFill); }

            for (int i = 0; i < MaxPanels; i++)
            {
                if ((asking || feedback) && i < game.Panels.Count) RenderPanel(i, feedback);
                else HidePanel(i);
            }

            if (phase == QuizPhase.Complete) RenderButton();
            else { Hide(buttonBorder); Hide(buttonFill); for (int p = 0; p < CvStateSlots; p++) Hide(buttonBar[p]); }
        }

        private void RenderPanel(int i, bool feedback)
        {
            Rect rect = panelRects[i];
            bool isCorrect = i == game.CorrectPanel;
            bool chosenByAnyone = false;
            for (int p = 0; p < players; p++)
                if (game.Players[p].Active && game.Players[p].Answered && game.Players[p].SelectedPanel == i) chosenByAnyone = true;

            Color fill = PanelFill, border = Outline;
            if (feedback)
            {
                if (isCorrect) { fill = CorrectTint; border = Correct; }
                else if (chosenByAnyone) { fill = WrongTint; border = Wrong; }
                else fill = PanelDim;
            }
            Place(panelBorder[i], Grow(rect, 0.035f), border);
            Place(panelFill[i], rect, fill);

            // A tick or a cross as well as the colour, so the result never depends on telling green from red.
            if (feedback && (isCorrect || chosenByAnyone))
            {
                panelIcon[i].sprite = isCorrect ? tickSprite : crossSprite;
                Place(panelIcon[i], IconRect(rect), isCorrect ? Correct : Wrong);
            }
            else Hide(panelIcon[i]);

            float barHeight = 0.13f, barGap = 0.04f;
            for (int p = 0; p < CvStateSlots; p++)
            {
                QuizPlayerState state = game.Players[p];
                bool active = p < players && state.Active;
                float y0 = rect.yMin + 0.07f + p * (barHeight + barGap);
                float share = 0;
                if (active && !feedback)
                {
                    if (state.Answered) share = state.SelectedPanel == i ? 1f : 0f;
                    else if (state.DwellPanel == i) share = (float)state.DwellProgress;
                }
                if (share > 0.001f)
                    Place(panelBar[i, p], Box(rect.xMin + 0.08f, y0, rect.xMin + 0.08f + (rect.width - 0.16f) * share, y0 + barHeight), PlayerColors[p]);
                else Hide(panelBar[i, p]);

                bool marker = active && state.Answered && state.SelectedPanel == i;
                if (marker)
                {
                    panelMarker[i, p].sprite = p == 0 ? discSprite : diamondSprite;
                    Place(panelMarker[i, p], MarkerRect(rect, p), PlayerColors[p]);
                }
                else Hide(panelMarker[i, p]);
            }
        }

        private void RenderButton()
        {
            Place(buttonBorder, Grow(buttonRect, 0.035f), Outline);
            Place(buttonFill, buttonRect, PanelFill);
            float barHeight = 0.13f, barGap = 0.04f;
            for (int p = 0; p < CvStateSlots; p++)
            {
                QuizPlayerState state = game.Players[p];
                float share = p < players && state.Active ? (float)state.DwellProgress : 0f;
                float y0 = buttonRect.yMin + 0.07f + p * (barHeight + barGap);
                if (share > 0.001f)
                    Place(buttonBar[p], Box(buttonRect.xMin + 0.08f, y0, buttonRect.xMin + 0.08f + (buttonRect.width - 0.16f) * share, y0 + barHeight), PlayerColors[p]);
                else Hide(buttonBar[p]);
            }
        }

        // ------------------------------------------------------------------------------------------------
        // Drawing the words
        // ------------------------------------------------------------------------------------------------

        private static string Hex(Color32 colour) => "#" + colour.r.ToString("X2") + colour.g.ToString("X2") + colour.b.ToString("X2");

        private Rect ToGui(Rect local)
        {
            Vector3 low = gameplayCamera.WorldToScreenPoint(World(local.xMin, local.yMin));
            Vector3 high = gameplayCamera.WorldToScreenPoint(World(local.xMax, local.yMax));
            return new Rect(low.x, Screen.height - high.y, high.x - low.x, high.y - low.y);
        }

        /// <summary>The biggest font size, down to <paramref name="minSize"/>, at which the text fits the box.</summary>
        private int Fit(string text, float width, float height, int maxSize, int minSize)
        {
            var content = new GUIContent(text);
            for (int size = maxSize; size > minSize; size -= 2)
            {
                textStyle.fontSize = size;
                if (textStyle.CalcHeight(content, width) <= height) return size;
            }
            return minSize;
        }

        private void Text(Rect local, string text, int maxSize, TextAnchor anchor, FontStyle style, Color colour, int minSize = 18)
        {
            Rect box = ToGui(local);
            textStyle.alignment = anchor;
            textStyle.fontStyle = style;
            textStyle.normal.textColor = colour;
            textStyle.fontSize = Fit(text, box.width, box.height, maxSize, minSize);
            GUI.Label(box, text, textStyle);
        }

        private void OnGUI()
        {
            if (Event.current.type != EventType.Repaint) return;
            if (textStyle == null)
                textStyle = new GUIStyle(GUI.skin.label) { wordWrap = true, richText = true, clipping = TextClipping.Overflow };
            if (loadError != null) { DrawMessage(loadError); return; }
            if (game == null || viewError != null) { DrawMessage(viewError ?? "Starting…"); return; }

            float scale = Screen.height / 1080f;
            int big = Mathf.RoundToInt(58 * scale), medium = Mathf.RoundToInt(44 * scale), small = Mathf.RoundToInt(30 * scale);
            DrawHud(small);
            switch (game.Phase)
            {
                case QuizPhase.Waiting: DrawWaiting(big, medium, small); break;
                case QuizPhase.Asking: DrawAsking(big, medium, small); break;
                case QuizPhase.Feedback: DrawFeedback(medium, small); break;
                default: DrawComplete(big, medium, small); break;
            }
            DrawCursorTags(small);
        }

        private void DrawMessage(string message)
        {
            var style = new GUIStyle(GUI.skin.label) { wordWrap = true, fontSize = Mathf.RoundToInt(Screen.height * 0.035f), alignment = TextAnchor.MiddleCenter };
            style.normal.textColor = Color.white;
            GUI.Box(new Rect(Screen.width * 0.1f, Screen.height * 0.35f, Screen.width * 0.8f, Screen.height * 0.3f), GUIContent.none);
            GUI.Label(new Rect(Screen.width * 0.12f, Screen.height * 0.37f, Screen.width * 0.76f, Screen.height * 0.26f), message, style);
        }

        private void DrawHud(int size)
        {
            string left = game.Phase == QuizPhase.Waiting ? "Scam Quiz"
                : game.Phase == QuizPhase.Complete ? "Round complete   |   Level " + game.Level
                : "Question " + (game.QuestionIndex + 1) + " of " + game.QuestionCount + "   |   Level " + game.Level;
            Text(hudRect, left, size, TextAnchor.MiddleLeft, FontStyle.Bold, Ink);
            if (game.Phase == QuizPhase.Asking)
            {
                string right = anyTracked ? "Time left: " + Mathf.CeilToInt((float)game.TimeRemaining) + " s"
                                          : "Clock paused: show a hand";
                Text(hudRect, right, size, TextAnchor.MiddleRight, FontStyle.Bold, game.TimeRemaining <= 5 && anyTracked ? Wrong : Ink);
            }
        }

        private void DrawWaiting(int big, int medium, int small)
        {
            var text = new StringBuilder("Show your hand to join\n\n");
            for (int p = 0; p < players; p++)
                text.Append("<color=").Append(Hex(PlayerColors[p])).Append("><b>Player ").Append(p + 1).Append("</b></color>:  ")
                    .Append(game.Players[p].Joined ? "ready" : "waiting for a hand").Append('\n');
            Text(Inset(cardRect, 0.3f, 0.2f), text.ToString(), medium, TextAnchor.MiddleCenter, FontStyle.Bold, Ink);
            Text(Box(cardRect.xMin + 0.3f, cardRect.yMin + 0.1f, cardRect.xMax - 0.3f, cardRect.yMin + 0.75f),
                 "Move your hand to move your cursor. Hold it on an answer to choose it. Press R to start with whoever is ready.",
                 small, TextAnchor.LowerCenter, FontStyle.Normal, Outline);
        }

        private void DrawAsking(int big, int medium, int small)
        {
            Text(Inset(cardRect, 0.35f, 0.25f), game.CurrentQuestion.Text, big, TextAnchor.MiddleLeft, FontStyle.Bold, Ink);
            DrawAnswerText(medium);
            DrawStatusHint(small);
        }

        private void DrawAnswerText(int size)
        {
            for (int i = 0; i < game.Panels.Count; i++)
            {
                string label = (char)('A' + i) + ".  " + game.Panels[i].Text;
                Text(AnswerTextRect(panelRects[i]), label, size, TextAnchor.MiddleLeft, FontStyle.Bold, Ink, 20);
            }
        }

        /// <summary>Tell each player, without words they must read in a hurry, whether their answer is in.</summary>
        private void DrawStatusHint(int size)
        {
            for (int p = 0; p < players; p++)
            {
                if (!game.Players[p].Active || !game.Players[p].Answered) continue;
                int number = game.Players[p].SelectedPanel;
                if (number < 0 || number >= game.Panels.Count) continue;
                Rect marker = MarkerRect(panelRects[number], p);
                Text(marker, (p + 1).ToString(), size, TextAnchor.MiddleCenter, FontStyle.Bold, p == 0 ? Color.white : Ink);
            }
        }

        private void DrawFeedback(int medium, int small)
        {
            var text = new StringBuilder();
            for (int p = 0; p < players; p++)
            {
                QuizPlayerState state = game.Players[p];
                if (!state.Active) continue;
                string result = state.Answered ? (state.IsCorrect ? "Well done!" : "Not quite. Here is why:") : "Time ran out. Here is why:";
                text.Append("<color=").Append(Hex(PlayerColors[p])).Append("><b>Player ").Append(p + 1).Append(":</b></color> ")
                    .Append(result).Append('\n');
            }
            text.Append('\n').Append(game.CurrentQuestion.Explanation);
            Text(Inset(cardRect, 0.35f, 0.25f), text.ToString(), medium, TextAnchor.MiddleLeft, FontStyle.Bold, Ink, 22);
            DrawAnswerText(medium);
            DrawStatusHint(small);
        }

        private void DrawComplete(int big, int medium, int small)
        {
            var text = new StringBuilder("Round complete\n\n");
            double groupCorrect = 0, groupTotal = 0;
            for (int p = 0; p < players; p++)
            {
                if (!game.Players[p].Active) continue;
                ScamQuizStats stats = game.Stats(p);
                groupCorrect += stats.Correct; groupTotal += stats.Total;
                text.Append("<color=").Append(Hex(PlayerColors[p])).Append("><b>Player ").Append(p + 1).Append("</b></color>:  ")
                    .Append(stats.Correct).Append(" of ").Append(stats.Total).Append(" right");
                if (stats.AverageResponseTime.HasValue) text.Append("   |   average ").Append(stats.AverageResponseTime.Value.ToString("0.0")).Append(" s");
                text.Append("   |   best run ").Append(stats.BestStreak).Append('\n');
            }
            text.Append('\n').Append(Encouragement(groupTotal > 0 ? groupCorrect / groupTotal : 0));
            if (levelNotice != null) text.Append('\n').Append(levelNotice);
            Text(Inset(cardRect, 0.35f, 0.25f), text.ToString(), medium, TextAnchor.MiddleLeft, FontStyle.Bold, Ink, 22);
            Text(buttonRect, "Play again", big, TextAnchor.MiddleCenter, FontStyle.Bold, Ink);
            Text(Box(buttonRect.xMin - 2.5f, buttonRect.yMin - 0.55f, buttonRect.xMax + 2.5f, buttonRect.yMin - 0.05f),
                 "Hold your cursor on the button, or press R.   " + DeliveryText(), small, TextAnchor.UpperCenter, FontStyle.Normal, Outline);
        }

        private static string Encouragement(double score)
        {
            if (score >= 0.8) return "Great job. You spotted the tricks.";
            if (score >= 0.5) return "Good work. A few more rounds will make these tricks easier to spot.";
            return "Thank you for playing. Each explanation helps you spot the next scam.";
        }

        /// <summary>A small number beside each cursor, so players can find their own at a glance.</summary>
        private void DrawCursorTags(int size)
        {
            for (int p = 0; p < players; p++)
            {
                if (!tracked[p]) continue;
                Vector3 screen = gameplayCamera.WorldToScreenPoint(cursors[p].transform.position);
                float box = size * 1.1f;
                var rect = new Rect(screen.x + size * 0.9f, Screen.height - screen.y - size * 1.9f, box, box);
                Color previous = GUI.color;
                GUI.color = PlayerColors[p];
                GUI.DrawTexture(rect, Texture2D.whiteTexture);
                GUI.color = previous;
                textStyle.alignment = TextAnchor.MiddleCenter;
                textStyle.fontStyle = FontStyle.Bold;
                textStyle.fontSize = size;
                textStyle.normal.textColor = p == 0 ? Color.white : Ink;
                GUI.Label(rect, (p + 1).ToString(), textStyle);
            }
        }

        private static Rect Inset(Rect rect, float x, float y) => Box(rect.xMin + x, rect.yMin + y, rect.xMax - x, rect.yMax - y);

        // ------------------------------------------------------------------------------------------------
        // Building the sprites (generated locally; no artwork)
        // ------------------------------------------------------------------------------------------------

        private void BuildVisuals()
        {
            whiteSprite = MakeSprite("white", 4, (x, y) => 1f);
            discSprite = MakeSprite("disc", 64, (x, y) => Mathf.Clamp01((1 - Mathf.Sqrt(x * x + y * y)) * 32f));
            diamondSprite = MakeSprite("diamond", 64, (x, y) => Mathf.Clamp01((1 - (Mathf.Abs(x) + Mathf.Abs(y))) * 32f));
            tickSprite = MakeSprite("tick", 64, (x, y) => Stroke(x, y, new[] { new Vector2(-0.55f, 0.0f), new Vector2(-0.18f, -0.4f), new Vector2(0.6f, 0.5f) }));
            crossSprite = MakeSprite("cross", 64, (x, y) => Mathf.Max(
                Stroke(x, y, new[] { new Vector2(-0.5f, -0.5f), new Vector2(0.5f, 0.5f) }),
                Stroke(x, y, new[] { new Vector2(-0.5f, 0.5f), new Vector2(0.5f, -0.5f) })));

            cardBorder = MakeQuad("Card Border", 8, whiteSprite);
            cardFillQuad = MakeQuad("Card", 9, whiteSprite);
            timerTrack = MakeQuad("Timer Track", 9, whiteSprite);
            timerFill = MakeQuad("Timer", 10, whiteSprite);
            buttonBorder = MakeQuad("Button Border", 8, whiteSprite);
            buttonFill = MakeQuad("Button", 9, whiteSprite);
            for (int p = 0; p < CvStateSlots; p++) buttonBar[p] = MakeQuad("Button Bar " + (p + 1), 12, whiteSprite);
            for (int i = 0; i < MaxPanels; i++)
            {
                panelBorder[i] = MakeQuad("Panel " + (i + 1) + " Border", 8, whiteSprite);
                panelFill[i] = MakeQuad("Panel " + (i + 1), 9, whiteSprite);
                panelIcon[i] = MakeQuad("Panel " + (i + 1) + " Result", 14, tickSprite);
                for (int p = 0; p < CvStateSlots; p++)
                {
                    panelBar[i, p] = MakeQuad("Panel " + (i + 1) + " Bar " + (p + 1), 12, whiteSprite);
                    panelMarker[i, p] = MakeQuad("Panel " + (i + 1) + " Marker " + (p + 1), 15, discSprite);
                }
            }
            HideEverything();
        }

        /// <summary>A white mask of the given size, one world unit across, where <paramref name="alpha"/> takes x and y from -1 to 1.</summary>
        private Sprite MakeSprite(string label, int size, Func<float, float, float> alpha)
        {
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false)
            { name = "MotionPlay quiz " + label, filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
            var pixels = new Color32[size * size];
            for (int py = 0; py < size; py++)
                for (int px = 0; px < size; px++)
                {
                    float x = (px + 0.5f - size / 2f) / (size / 2f), y = (py + 0.5f - size / 2f) / (size / 2f);
                    pixels[py * size + px] = new Color32(255, 255, 255, (byte)(255 * Mathf.Clamp01(alpha(x, y))));
                }
            texture.SetPixels32(pixels);
            texture.Apply(false);
            Sprite sprite = Sprite.Create(texture, new Rect(0, 0, size, size), new Vector2(0.5f, 0.5f), size, 0, SpriteMeshType.FullRect);
            sprite.name = "MotionPlay quiz " + label;
            textures.Add(texture);
            sprites.Add(sprite);
            return sprite;
        }

        /// <summary>1 close to the line through the points, fading to 0 at its edge.</summary>
        private static float Stroke(float x, float y, Vector2[] points)
        {
            const float halfWidth = 0.17f;
            float nearest = float.MaxValue;
            for (int i = 0; i + 1 < points.Length; i++)
            {
                Vector2 a = points[i], b = points[i + 1], p = new Vector2(x, y);
                float t = Mathf.Clamp01(Vector2.Dot(p - a, b - a) / (b - a).sqrMagnitude);
                nearest = Mathf.Min(nearest, Vector2.Distance(p, a + (b - a) * t));
            }
            return Mathf.Clamp01((halfWidth - nearest) * 24f);
        }

        private SpriteRenderer MakeQuad(string objectName, int order, Sprite sprite)
        {
            var child = new GameObject(objectName);
            child.transform.SetParent(transform, false);
            var renderer = child.AddComponent<SpriteRenderer>();
            renderer.sprite = sprite;
            renderer.sortingOrder = order; // Below the hand cursors, which use order 100.
            renderer.enabled = false;
            return renderer;
        }

        private void OnDestroy()
        {
            foreach (Sprite sprite in sprites) if (sprite != null) Destroy(sprite);
            foreach (Texture2D texture in textures) if (texture != null) Destroy(texture);
        }
    }
}
