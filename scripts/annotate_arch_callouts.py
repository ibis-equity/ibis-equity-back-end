from PIL import Image, ImageDraw, ImageFont

SRC = "architecture/arch_aoss_rag.png"
OUT = "architecture/arch_aoss_rag.png"

img = Image.open(SRC).convert("RGBA")
overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)

try:
    font_title = ImageFont.truetype("arial.ttf", 13)
    font_body = ImageFont.truetype("arial.ttf", 12)
except OSError:
    font_title = ImageFont.load_default()
    font_body = ImageFont.load_default()


def callout(x, y, w, h, title, lines):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(255, 255, 240, 215), outline=(64, 64, 64, 220), width=2)
    draw.text((x + 8, y + 6), title, font=font_title, fill=(15, 23, 42, 255))
    ty = y + 26
    for line in lines:
        draw.text((x + 8, ty), line, font=font_body, fill=(31, 41, 55, 255))
        ty += 16


callout(10, 405, 238, 78, "User", [
    "Loc: Far-left entry point",
    "Purpose: Start interactions",
    "Function: Submit auth + queries",
])

callout(130, 450, 260, 78, "Application Load Balancer", [
    "Loc: Left-center",
    "Purpose: Public ingress",
    "Function: Route traffic to app/auth",
])

callout(335, 260, 250, 78, "Cognito Login UI", [
    "Loc: Upper-left",
    "Purpose: User authentication",
    "Function: Login/token issuance",
])

callout(260, 705, 310, 78, "ECS Fargate Service", [
    "Loc: Lower-left",
    "Purpose: Run Streamlit RAG app",
    "Function: Query orchestration + UI",
])

callout(390, 540, 285, 78, "OpenSearch Service Index", [
    "Loc: Mid-left",
    "Purpose: Vector retrieval store",
    "Function: Similarity search over docs",
])

callout(690, 705, 300, 78, "Amazon Bedrock", [
    "Loc: Bottom-center",
    "Purpose: Model + embeddings API",
    "Function: Generate answers/embeddings",
])

callout(1210, 125, 320, 78, "CDK S3 Bucket Deployment", [
    "Loc: Top-right",
    "Purpose: Seed docs from repo",
    "Function: Upload local PDFs to S3",
])

callout(850, 330, 300, 78, "Knowledgebase S3 Bucket", [
    "Loc: Upper-mid-right",
    "Purpose: Store source PDFs",
    "Function: Emit ObjectCreated events",
])

callout(1220, 330, 305, 78, "Lambda: pdf-processor", [
    "Loc: Upper-right",
    "Purpose: Convert PDF to text",
    "Function: Write .txt to processed bucket",
])

callout(1260, 620, 320, 78, "Processed Text S3 Bucket", [
    "Loc: Mid-right",
    "Purpose: Hold extracted text",
    "Function: Trigger downstream indexing",
])

callout(1080, 705, 300, 78, "Lambda: aoss-trigger", [
    "Loc: Mid-right",
    "Purpose: Bridge to queue",
    "Function: Send bucket/key to SQS",
])

callout(920, 620, 260, 78, "SQS AOSS_Update_Queue", [
    "Loc: Center-right",
    "Purpose: Decouple indexing",
    "Function: Buffer index update jobs",
])

callout(600, 620, 300, 78, "Lambda: aoss-update", [
    "Loc: Mid-center",
    "Purpose: Build and store vectors",
    "Function: Embed + index in OpenSearch",
])

result = Image.alpha_composite(img, overlay).convert("RGB")
result.save(OUT)
print(f"Annotated diagram written to {OUT}")
