from PIL import Image, ImageDraw, ImageFont

SRC = "architecture/arch_aoss_rag_base.png"
OUT = "architecture/arch_aoss_rag.png"

base = Image.open(SRC).convert("RGB")
bw, bh = base.size
legend_w = 760
margin = 20

canvas = Image.new("RGB", (bw + legend_w, bh), (244, 246, 248))
canvas.paste(base, (0, 0))

draw = ImageDraw.Draw(canvas)

try:
    f_title = ImageFont.truetype("arial.ttf", 28)
    f_head = ImageFont.truetype("arial.ttf", 17)
    f_body = ImageFont.truetype("arial.ttf", 14)
    f_num = ImageFont.truetype("arial.ttf", 16)
except OSError:
    f_title = ImageFont.load_default()
    f_head = ImageFont.load_default()
    f_body = ImageFont.load_default()
    f_num = ImageFont.load_default()

# Numbered components and marker positions on original image area
components = [
    (1, "User", "Far-left", "Start user interaction", "Submits login and chat requests", (55, 355)),
    (2, "Application Load Balancer", "Left-center", "Public ingress point", "Routes traffic to app and auth flow", (205, 355)),
    (3, "Cognito Login UI", "Upper-left", "Authenticate users", "Handles login and token issuance", (450, 195)),
    (4, "ECS Fargate Service", "Lower-left", "Run RAG app", "Hosts Streamlit/LangChain service", (360, 610)),
    (5, "OpenSearch Service Index", "Mid-left", "Vector retrieval store", "Stores and serves semantic vectors", (490, 445)),
    (6, "Amazon Bedrock", "Bottom-center", "Model + embeddings", "Generates answers and embeddings", (810, 705)),
    (7, "CDK S3 Bucket Deployment", "Top-right", "Seed docs from repo", "Uploads local PDFs on deploy", (1310, 48)),
    (8, "Knowledgebase S3 Bucket", "Upper-mid-right", "Store source PDFs", "Emits ObjectCreated events", (970, 225)),
    (9, "Lambda: pdf-processor", "Upper-right", "Extract PDF text", "Converts PDF to .txt artifacts", (1250, 225)),
    (10, "Processed Text S3 Bucket", "Mid-right", "Store text artifacts", "Triggers downstream indexing path", (1490, 430)),
    (11, "Lambda: aoss-trigger", "Mid-right", "Bridge to queue", "Sends bucket/key details to SQS", (1190, 430)),
    (12, "SQS AOSS_Update_Queue", "Center-right", "Decouple indexing", "Buffers and schedules index jobs", (990, 430)),
    (13, "Lambda: aoss-update", "Mid-center", "Index vectors", "Embeds and upserts into OpenSearch", (740, 430)),
]

# Draw numbered badges
for n, _, _, _, _, (x, y) in components:
    r = 16
    draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255), outline=(31, 41, 55), width=3)
    t = str(n)
    tw, th = draw.textbbox((0, 0), t, font=f_num)[2:4]
    draw.text((x - tw / 2, y - th / 2 - 1), t, font=f_num, fill=(17, 24, 39))

# Legend panel
lx = bw + margin
ly = margin
lw = legend_w - (2 * margin)
lh = bh - (2 * margin)
draw.rounded_rectangle((lx, ly, lx + lw, ly + lh), radius=14, fill=(255, 255, 255), outline=(148, 163, 184), width=2)

draw.text((lx + 16, ly + 14), "Architecture Component Legend", font=f_title, fill=(15, 23, 42))
draw.text((lx + 16, ly + 52), "Name | Location | Purpose | Function", font=f_head, fill=(51, 65, 85))

line_h = 16
col_x = [lx + 16, lx + 380]
col_idx = 0
cy_start = ly + 86
cy = cy_start

for n, name, loc, purpose, function, _ in components:
    if cy > ly + lh - 90:
        if col_idx == 0:
            col_idx = 1
            cy = cy_start
        else:
            break
    x = col_x[col_idx]
    head = f"{n}. {name}"
    draw.text((x, cy), head, font=f_head, fill=(17, 24, 39))
    cy += line_h + 2
    draw.text((x + 12, cy), f"Location: {loc}", font=f_body, fill=(55, 65, 81))
    cy += line_h
    draw.text((x + 12, cy), f"Purpose: {purpose}", font=f_body, fill=(55, 65, 81))
    cy += line_h
    draw.text((x + 12, cy), f"Function: {function}", font=f_body, fill=(55, 65, 81))
    cy += line_h + 8

canvas.save(OUT)
print(f"Wrote numbered annotated diagram to {OUT}")
