using System;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Threading;

namespace MotionPlay.Networking
{
    /// <summary>
    /// Socket worker with no Unity calls; snapshots are the only data handoff. Keeps one buffer per player (slot),
    /// each with its own stream and ordering rules, so two players' streams never compete. A packet without a slot
    /// (the original single-player stream) goes to slot 0, and <see cref="Read()"/> reads slot 0, so single-player
    /// code is unchanged.
    /// </summary>
    public sealed class UdpStateListener : IDisposable
    {
        private readonly ReceiverConfiguration configuration;
        private readonly object lifecycle = new object();
        private CvStateBuffer[] buffers;
        private Socket socket;
        private Thread worker;
        private volatile bool running;
        private string lastError;

        public bool IsRunning => running;
        public string LastError => Volatile.Read(ref lastError);
        public static double MonotonicSeconds => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;

        public UdpStateListener(ReceiverConfiguration configuration)
        {
            this.configuration = configuration ?? throw new ArgumentNullException(nameof(configuration));
            buffers = NewBuffers();
        }

        private CvStateBuffer[] NewBuffers()
        {
            var created = new CvStateBuffer[CvState.MaxSlots];
            for (int slot = 0; slot < created.Length; slot++) created[slot] = new CvStateBuffer(configuration.TimeoutSeconds);
            return created;
        }

        public void Start()
        {
            lock (lifecycle)
            {
                if (running) return;
                if (worker != null) throw new InvalidOperationException("Stop the previous listener before restarting.");
                var nextSocket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
                try
                {
                    nextSocket.ExclusiveAddressUse = true;
                    nextSocket.ReceiveTimeout = 100;
                    nextSocket.Bind(new IPEndPoint(IPAddress.Loopback, configuration.Port));
                    buffers = NewBuffers();
                    Volatile.Write(ref lastError, null);
                    socket = nextSocket;
                    running = true;
                    CvStateBuffer[] nextBuffers = buffers;
                    worker = new Thread(() => ReceiveLoop(nextSocket, nextBuffers))
                    { IsBackground = true, Name = "MotionPlay UDP receive" };
                    worker.Start();
                }
                catch
                {
                    running = false;
                    worker = null;
                    socket = null;
                    nextSocket.Dispose();
                    throw;
                }
            }
        }

        /// <summary>The latest state of player 0, which is the whole story for a single-player stream.</summary>
        public ReceiverSnapshot Read() => Read(0);

        /// <summary>The latest state of one player (0 or 1). A player who never sent has no state.</summary>
        public ReceiverSnapshot Read(int slot)
        {
            if (slot < 0 || slot >= CvState.MaxSlots) throw new ArgumentOutOfRangeException(nameof(slot));
            return buffers[slot].Read(MonotonicSeconds);
        }

        private static void RecordInvalid(CvStateBuffer[] ownedBuffers)
        {
            // Not attributable to a player, so every player's snapshot shows it.
            foreach (CvStateBuffer each in ownedBuffers) each.RecordInvalid();
        }

        private void ReceiveLoop(Socket ownedSocket, CvStateBuffer[] ownedBuffers)
        {
            var bytes = new byte[CvStateCodec.MaxDatagramBytes + 1];
            EndPoint source = new IPEndPoint(IPAddress.Any, 0);
            try
            {
                while (running)
                {
                    int count;
                    try { count = ownedSocket.ReceiveFrom(bytes, ref source); }
                    catch (SocketException error) when (error.SocketErrorCode == SocketError.TimedOut ||
                        error.SocketErrorCode == SocketError.WouldBlock) { continue; }
                    catch (SocketException error) when (error.SocketErrorCode == SocketError.MessageSize)
                    { RecordInvalid(ownedBuffers); continue; }
                    if (!running) break;
                    if (!(source is IPEndPoint sender) || !IPAddress.IsLoopback(sender.Address) ||
                        !CvStateCodec.TryDecode(bytes, count, out CvState state))
                    { RecordInvalid(ownedBuffers); continue; }
                    ownedBuffers[state.Slot ?? 0].TryAccept(state, MonotonicSeconds);
                }
            }
            // A worker boundary must report failures to the owner, rather than
            // let an unhandled background exception terminate the Unity process.
            catch (Exception)
            {
                if (running) Volatile.Write(ref lastError, "UDP reception stopped; disable/re-enable the receiver and check the port/firewall.");
            }
            finally
            {
                running = false;
                ClearAll(ownedBuffers);
                ownedSocket.Dispose();
            }
        }

        /// <summary>Close to unblock receive, then join with a finite wait; safe to repeat.</summary>
        public void Stop()
        {
            lock (lifecycle)
            {
                running = false;
                socket?.Dispose();
                socket = null;
                if (worker != null && !worker.Join(1000))
                {
                    Volatile.Write(ref lastError, "UDP worker did not stop within one second; restart Play Mode before rebinding.");
                    ClearAll(buffers);
                    return;
                }
                worker = null;
                ClearAll(buffers);
            }
        }

        private static void ClearAll(CvStateBuffer[] all)
        {
            foreach (CvStateBuffer each in all) each.ClearState();
        }

        public void Dispose() => Stop();
    }
}
