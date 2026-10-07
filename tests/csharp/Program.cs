using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using MotionPlay.Control;
using MotionPlay.Games;
using MotionPlay.Networking;
using MotionPlay.Tests;
using Newtonsoft.Json.Linq;
using NUnitLite;

/// <summary>Runs the same core tests outside Unity; probe mode supports Python interoperability checks.</summary>
internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--probe-slots") return ProbeSlots(int.Parse(args[1]));
        if (args.Length == 2 && args[0] == "--emit-quiz-result") return EmitQuizResult(args[1]);
        if (args.Length != 2 || args[0] != "--probe") return new AutoRun().Execute(args);
        using (var listener = new UdpStateListener(new ReceiverConfiguration(int.Parse(args[1]))))
        {
            listener.Start();
            Console.WriteLine("READY");
            long lastAccepted = 0;
            var clock = Stopwatch.StartNew();
            while (clock.Elapsed.TotalSeconds < 10)
            {
                var snapshot = listener.Read();
                if (snapshot.Accepted > lastAccepted && snapshot.State != null)
                {
                    lastAccepted = snapshot.Accepted;
                    var state = snapshot.State;
                    CursorMapper.TryMap(snapshot, CursorArea.ForOrthographic(3, 4.0 / 3, 0.12, 0.08),
                                        true, out CursorPoint cursor);
                    Console.WriteLine(new JObject
                    {
                        ["tracking"] = state.Tracking, ["sequence"] = state.Sequence,
                        ["gesture"] = state.Gesture, ["stream_id"] = state.StreamId,
                        ["accepted"] = snapshot.Accepted, ["invalid"] = snapshot.Invalid,
                        ["cursor"] = cursor == null ? (JToken)JValue.CreateNull() :
                            new JObject { ["x"] = cursor.X, ["y"] = cursor.Y },
                        ["position"] = state.Position == null ? (JToken)JValue.CreateNull() :
                            new JObject { ["x"] = state.Position.X, ["y"] = state.Position.Y, ["z"] = state.Position.Z }
                    }.ToString(Newtonsoft.Json.Formatting.None));
                }
                if (lastAccepted > 0 && snapshot.State == null)
                {
                    Console.WriteLine("TIMEOUT");
                    return 0;
                }
                if (!listener.IsRunning) return 2;
                Thread.Sleep(2);
            }
            return 1;
        }
    }

    /// <summary>
    /// Plays a whole two-player round from the given question file with the real game code, then prints each player's
    /// QUIZ_SESSION_END as the Unity quiz would send it, so Python can check what it would receive.
    /// </summary>
    private static int EmitQuizResult(string questionFile)
    {
        QuestionBank bank = QuestionBank.Parse(File.ReadAllText(questionFile));
        var game = new ScamQuizGame(bank, ScamQuizDifficulty.SettingsFor(2), 3);
        game.Step(QuizDriver.Frame, QuizDriver.Hands(true, true));
        QuizDriver.PlayRound(game, q => true, q => q % 2 == 0, think: 1); // player 1 always right, player 2 on every other
        string round = Guid.NewGuid().ToString("D"), stream = Guid.NewGuid().ToString("D");
        for (int slot = 0; slot < 2; slot++)
        {
            QuizSessionResult result = QuizSessionResult.FromStats(game.Stats(slot), slot, slot == 0 ? "left" : "right",
                ScamQuizDifficulty.LabelFor(game.Level), Guid.NewGuid().ToString("D"), round, bank.Version,
                1770000000000, 1770000090000);
            result.StreamId = stream; result.Sequence = slot; result.Timestamp = 1770000091000;
            Console.WriteLine(Encoding.UTF8.GetString(QuizSessionResultCodec.Encode(result)));
        }
        return 0;
    }

    /// <summary>Two-player probe: prints each accepted packet with the player it belongs to.</summary>
    private static int ProbeSlots(int port)
    {
        using (var listener = new UdpStateListener(new ReceiverConfiguration(port)))
        {
            listener.Start();
            Console.WriteLine("READY");
            var lastAccepted = new long[CvState.MaxSlots];
            bool seenAny = false;
            var clock = Stopwatch.StartNew();
            while (clock.Elapsed.TotalSeconds < 10)
            {
                bool anyFresh = false;
                for (int slot = 0; slot < CvState.MaxSlots; slot++)
                {
                    var snapshot = listener.Read(slot);
                    if (snapshot.State != null) anyFresh = true;
                    if (snapshot.Accepted <= lastAccepted[slot] || snapshot.State == null) continue;
                    lastAccepted[slot] = snapshot.Accepted;
                    seenAny = true;
                    var state = snapshot.State;
                    CursorMapper.TryMap(snapshot, CursorArea.ForOrthographic(3, 4.0 / 3, 0.12, 0.08), true, out CursorPoint cursor);
                    Console.WriteLine(new JObject
                    {
                        ["slot"] = slot, ["packet_slot"] = state.Slot.HasValue ? (JToken)state.Slot.Value : JValue.CreateNull(),
                        ["tracking"] = state.Tracking, ["sequence"] = state.Sequence, ["hand"] = state.Hand,
                        ["gesture"] = state.Gesture, ["stream_id"] = state.StreamId,
                        ["cursor"] = cursor == null ? (JToken)JValue.CreateNull() : new JObject { ["x"] = cursor.X, ["y"] = cursor.Y },
                        ["position"] = state.Position == null ? (JToken)JValue.CreateNull() :
                            new JObject { ["x"] = state.Position.X, ["y"] = state.Position.Y, ["z"] = state.Position.Z }
                    }.ToString(Newtonsoft.Json.Formatting.None));
                }
                if (seenAny && !anyFresh) { Console.WriteLine("TIMEOUT"); return 0; }
                if (!listener.IsRunning) return 2;
                Thread.Sleep(2);
            }
            return 1;
        }
    }
}
