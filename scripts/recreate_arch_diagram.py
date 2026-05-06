from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1800, 920
BG = (245, 247, 250)

img = Image.new("RGB", (WIDTH, HEIGHT), BG)
draw = ImageDraw.Draw(img)

try:
    font_title = ImageFont.truetype("arial.ttf", 36)
    font_node = ImageFont.truetype("arial.ttf", 20)
    font_small = ImageFont.truetype("arial.ttf", 16)
except OSError:
    font_title = ImageFont.load_default()
    font_node = ImageFont.load_default()
    font_small = ImageFont.load_default()


def box(x, y, w, h, title, subtitle="", fill=(255, 255, 255), outline=(60, 75, 97)):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=16, fill=fill, outline=outline, width=3)
    draw.text((x + 14, y + 12), title, font=font_node, fill=(24, 34, 48))
    if subtitle:
        draw.text((x + 14, y + 44), subtitle, font=font_small, fill=(51, 65, 85))


def arrow(x1, y1, x2, y2, label=""):
    draw.line((x1, y1, x2, y2), fill=(44, 62, 80), width=3)
    # arrow head
    if x2 >= x1:
        head = [(x2, y2), (x2 - 12, y2 - 6), (x2 - 12, y2 + 6)]
    else:
        head = [(x2, y2), (x2 + 12, y2 - 6), (x2 + 12, y2 + 6)]
    draw.polygon(head, fill=(44, 62, 80))
    if label:
        lx = (x1 + x2) // 2
        ly = (y1 + y2) // 2 - 20
        draw.text((lx, ly), label, font=font_small, fill=(31, 41, 55))


# Title

draw.text((40, 24), "RAG + OpenSearch Architecture (Recreated)", font=font_title, fill=(15, 23, 42))

# Section labels

draw.text((42, 86), "User & App Path", font=font_node, fill=(30, 41, 59))
draw.text((880, 86), "Document Ingestion Path", font=font_node, fill=(30, 41, 59))

# Left side components
box(40, 160, 210, 90, "User", "Browser client", fill=(240, 249, 255), outline=(2, 132, 199))
box(290, 160, 280, 90, "Application Load Balancer", "Ingress + routing", fill=(255, 247, 237), outline=(234, 88, 12))
box(630, 90, 230, 90, "Cognito Login UI", "Authentication", fill=(254, 242, 242), outline=(220, 38, 38))
box(330, 610, 380, 100, "ECS Fargate Service", "Streamlit + LangChain RAG app", fill=(255, 247, 237), outline=(234, 88, 12))
box(520, 350, 300, 120, "Amazon OpenSearch Service Index", "Vector store for retrieval", fill=(245, 243, 255), outline=(124, 58, 237))
box(760, 780, 250, 90, "Amazon Bedrock", "LLM + embedding APIs", fill=(236, 253, 245), outline=(5, 150, 105))

# Right side components
box(1010, 90, 280, 90, "CDK S3 Bucket Deployment", "Seeds PDFs from local folder", fill=(250, 245, 255), outline=(192, 38, 211))
box(1010, 210, 280, 100, "Knowledgebase S3 Bucket", "Original PDF documents", fill=(240, 253, 244), outline=(22, 163, 74))
box(1350, 210, 280, 100, "Lambda: pdf-processor", "Extract PDF text to .txt", fill=(255, 247, 237), outline=(234, 88, 12))
box(1350, 420, 280, 100, "Processed Text S3 Bucket", "Plain text artifacts", fill=(240, 253, 244), outline=(22, 163, 74))
box(1010, 420, 280, 100, "Lambda: aoss-trigger", "S3 event -> SQS message", fill=(255, 247, 237), outline=(234, 88, 12))
box(1010, 610, 280, 100, "SQS AOSS_Update_Queue", "Decoupled indexing jobs", fill=(253, 242, 248), outline=(219, 39, 119))
box(680, 610, 280, 100, "Lambda: aoss-update", "Embeds + indexes docs", fill=(255, 247, 237), outline=(234, 88, 12))

# Arrows - app path
arrow(250, 205, 290, 205, "HTTP")
arrow(570, 205, 630, 135, "Login request")
arrow(430, 250, 430, 610, "After auth")
arrow(710, 660, 520, 420, "Vector search/update")
arrow(710, 660, 760, 825, "Bedrock calls")

# Arrows - ingestion path
arrow(1150, 180, 1150, 210, "Deploy seed docs")
arrow(1290, 260, 1350, 260, "S3 ObjectCreated")
arrow(1630, 260, 1630, 470)
arrow(1630, 470, 1490, 470, "TXT upload")
arrow(1350, 470, 1290, 470, "S3 ObjectCreated")
arrow(1150, 520, 1150, 610, "Queue message")
arrow(1010, 660, 960, 660, "Trigger lambda")
arrow(960, 660, 820, 660, "Index update")

# Save
img.save("architecture/arch_aoss_rag.png")
print("Wrote architecture/arch_aoss_rag.png")
