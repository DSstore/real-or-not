using UnityEngine;

namespace MotionPlay.Unity
{
    /// <summary>The outline of a hand cursor. Players differ by shape as well as colour, so they can be told apart without colour.</summary>
    public enum CursorShape { Disc, Diamond }

    /// <summary>Original geometric placeholder: a one-world-unit cursor shape, generated locally without artwork.</summary>
    [DisallowMultipleComponent]
    [RequireComponent(typeof(SpriteRenderer))]
    public sealed class CursorDisc : MonoBehaviour
    {
        [SerializeField] private Color32 fillColor = new Color32(78, 221, 181, 255);
        [SerializeField] private Color32 rimColor = new Color32(220, 255, 245, 255);
        [SerializeField] private CursorShape shape = CursorShape.Disc;

        private Texture2D texture;
        private Sprite sprite;

        /// <summary>Choose the cursor's look before Play; serialized so the setup survives scene reloads.</summary>
        public void Configure(Color32 fill, Color32 rim, CursorShape cursorShape)
        {
            fillColor = fill; rimColor = rim; shape = cursorShape;
        }

        private void Awake()
        {
            const int size = 64;
            texture = new Texture2D(size, size, TextureFormat.RGBA32, false)
            { name = "MotionPlay cursor", filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
            var pixels = new Color32[size * size];
            for (int y = 0; y < size; y++)
                for (int x = 0; x < size; x++)
                {
                    float dx = (x + 0.5f - size / 2f) / (size / 2f);
                    float dy = (y + 0.5f - size / 2f) / (size / 2f);
                    // Distance from the centre in the chosen shape: 1 is the edge of a disc or of a diamond.
                    float radius = shape == CursorShape.Diamond
                        ? Mathf.Abs(dx) + Mathf.Abs(dy) : Mathf.Sqrt(dx * dx + dy * dy);
                    byte alpha = (byte)(255 * Mathf.Clamp01((1 - radius) * size / 2f));
                    Color32 colour = radius > 0.82f ? rimColor : fillColor;
                    pixels[y * size + x] = new Color32(colour.r, colour.g, colour.b, alpha);
                }
            texture.SetPixels32(pixels);
            texture.Apply(false);
            sprite = Sprite.Create(texture, new Rect(0, 0, size, size), new Vector2(0.5f, 0.5f), size,
                                   0, SpriteMeshType.FullRect);
            sprite.name = "MotionPlay cursor";
            GetComponent<SpriteRenderer>().sprite = sprite;
        }

        private void OnDestroy()
        {
            if (sprite != null) Destroy(sprite);
            if (texture != null) Destroy(texture);
        }
    }
}
