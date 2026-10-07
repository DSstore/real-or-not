using System;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MotionPlay.Networking
{
    /// <summary>Validate the Python version-1 packet before it reaches game code.</summary>
    public static class CvStateCodec
    {
        public const int MaxDatagramBytes = 1200;
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);

        public static bool TryDecode(byte[] bytes, int count, out CvState state)
        {
            state = null;
            if (bytes == null || count < 1 || count > MaxDatagramBytes || count > bytes.Length)
                return false;
            try
            {
                string text = StrictUtf8.GetString(bytes, 0, count);
                using (var reader = new PacketJsonReader(new StringReader(text)))
                {
                    var root = JToken.ReadFrom(reader, new JsonLoadSettings
                    {
                        DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error,
                        CommentHandling = CommentHandling.Load
                    }) as JObject;
                    if (root == null || reader.Read()) return false; // No concatenated JSON objects.
                    if (root.Descendants().Any(token => token.Type == JTokenType.Float &&
                        !Finite(token.Value<double>()))) return false;
                    if (Text(root, "type") != "CV_STATE" || Integer(root, "version") != 1) return false;

                    string stream = Text(root, "stream_id");
                    if (!Guid.TryParseExact(stream, "D", out Guid id) || id.ToString("D") != stream) return false;
                    long sequence = Integer(root, "sequence");
                    long timestamp = Integer(root, "timestamp");
                    if (sequence < 0 || timestamp < 0) return false;
                    string hand = Text(root, "hand");
                    if (hand != "left" && hand != "right") return false;
                    string gesture = Text(root, "gesture");
                    if (gesture != "OPEN_HAND" && gesture != "FIST" && gesture != "PINCH" &&
                        gesture != "POINT" && gesture != "UNKNOWN") return false;
                    bool tracking = Boolean(root, "tracking");
                    bool mirrored = Boolean(root, "mirrored");
                    double confidence = Number(root, "confidence");
                    if (confidence < 0 || confidence > 1) return false;
                    JToken positionToken = Required(root, "position");
                    PalmPosition position = null;
                    if (tracking)
                    {
                        var value = positionToken as JObject;
                        if (value == null) return false;
                        double x = Number(value, "x"), y = Number(value, "y"), z = Number(value, "z");
                        if (x < 0 || x > 1 || y < 0 || y > 1) return false;
                        position = new PalmPosition(x, y, z);
                    }
                    else if (positionToken.Type != JTokenType.Null || gesture != "UNKNOWN" || confidence != 0)
                        return false;
                    // Optional: only two-player streams carry a slot. An explicit null counts as absent.
                    int? slot = null;
                    JToken slotToken = root["slot"];
                    if (slotToken != null && slotToken.Type != JTokenType.Null)
                    {
                        if (slotToken.Type != JTokenType.Integer) return false;
                        long slotValue = slotToken.Value<long>();
                        if (slotValue < 0 || slotValue >= CvState.MaxSlots) return false;
                        slot = (int)slotValue;
                    }
                    state = new CvState(stream, sequence, timestamp, hand, tracking, position,
                                        gesture, confidence, mirrored, slot);
                    return true;
                }
            }
            catch (Exception error) when (error is JsonException || error is ArgumentException ||
                error is FormatException || error is OverflowException || error is InvalidCastException)
            {
                return false;
            }
        }

        private static JToken Required(JObject root, string name)
        {
            return root[name] ?? throw new FormatException("Missing packet field.");
        }

        private static string Text(JObject root, string name)
        {
            JToken token = Required(root, name);
            if (token.Type != JTokenType.String) throw new FormatException("Expected string.");
            return token.Value<string>();
        }

        private static long Integer(JObject root, string name)
        {
            JToken token = Required(root, name);
            if (token.Type != JTokenType.Integer) throw new FormatException("Expected integer.");
            return token.Value<long>();
        }

        private static bool Boolean(JObject root, string name)
        {
            JToken token = Required(root, name);
            if (token.Type != JTokenType.Boolean) throw new FormatException("Expected boolean.");
            return token.Value<bool>();
        }

        private static double Number(JObject root, string name)
        {
            JToken token = Required(root, name);
            if (token.Type != JTokenType.Float && token.Type != JTokenType.Integer)
                throw new FormatException("Expected number.");
            double value = token.Value<double>();
            if (!Finite(value)) throw new FormatException("Expected finite number.");
            return value;
        }

        private static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

        // Json.NET supports non-JSON extensions. Disallow comments, unquoted
        // property names, and single quotes on ingress, including extra fields.
        private sealed class PacketJsonReader : JsonTextReader
        {
            public PacketJsonReader(TextReader reader) : base(reader)
            {
                DateParseHandling = DateParseHandling.None;
                FloatParseHandling = FloatParseHandling.Double;
                MaxDepth = 16;
            }

            public override bool Read()
            {
                bool result = base.Read();
                if (TokenType == JsonToken.Comment || TokenType == JsonToken.Undefined ||
                    TokenType == JsonToken.StartConstructor || TokenType == JsonToken.EndConstructor ||
                    ((TokenType == JsonToken.String || TokenType == JsonToken.PropertyName) && QuoteChar != '"'))
                    throw new JsonReaderException("Unsupported JSON extension.");
                return result;
            }
        }
    }
}
