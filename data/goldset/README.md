# 金标准集（N0 交付物）

> 🔴 **本目录是甲方内部资产，含个人信息，禁止提交到任何代码仓库 / 云盘 / 外部工具。**
> 交付乙方前先跑 `goldset.py seal` 把明文联系方式替换为盐化哈希。

## 本批规模

| 项目 | 数量 | 用途 |
| --- | :-: | --- |
| 简历标注集 | 200 份（其中图片/扫描件 40 份） | IDX-02 字段级准确率 ≥95% |
| 查询-人才集 | 50 条 | IDX-01 检索 Recall@10 ≥85% |

## 怎么用

1. **先读口径**：`docs/金标准集标注规范.md`（字段怎么填、缺失怎么标、分母怎么算）。**不看口径直接标，200 份标完基本要返工。**
2. 用 Excel / WPS 打开 `resume_annotations.csv`，按行填字段列。
   - 字段列**留空 = 还没标**；简历上确实没有该信息请填 `缺失`。
   - `status` 从 `pending` → `annotating` → `done`（只有 `done` 的行参与达标统计）。
   - 填完一行就写 `annotator`（姓名）与 `annotated_at`（日期），便于追溯与工作量核算。
3. `query_set.csv` 同法填写；`expected_resume_ids` 填**简历集的 `resume_id`**
   （如 `RS-0007`，多条用 `|` 分隔），❌ 不要填数据库 ID —— 标注阶段档案尚未入库。
4. 每完成 50 份跑一次质检：

```bash
cd backend
.venv/Scripts/python.exe scripts/goldset.py validate
```

## 标注工作量（需求 §12.5 估算，供排期参考）

- 简历标注集：**6~8 人天**（约 1.5~2 人天 / 50 份，含逐字段核对）
- 查询-人才集：**2~3 人天**
- 责任方：甲方 HR 专家 + 业务专家（**乙方无业务判断力**，D-11）

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `resume_annotations.csv` | 简历标注集（工具生成骨架，人工填写） |
| `query_set.csv` | 查询-人才集（工具生成骨架，人工填写） |
| `RESUME_CHECK.md` / `QUERY_CHECK.md` | 质检报告（`goldset.py validate --report` 生成） |
| `sealed/` | `goldset.py seal` 产出的可交付版本（明文联系方式已替换为哈希） |

## 文件类型取值

- `text_pdf`：文本层可直接提取的 PDF
- `text_docx`：Word 文档（.docx；.doc 请先另存为 .docx）
- `text_other`：其它文本格式（txt / 纯文本粘贴）
- `scan_image`：图片件（jpg / png 截图或拍照）
- `scan_pdf`：扫描件 PDF（无文本层，需 OCR）

## 状态取值

- `pending`：未开始
- `annotating`：标注中（半成品，不计入质检达标）
- `done`：已完成（质检只对 `done` 的行算达标）
- `reviewed`：已复核（第二人抽查通过）

## 字段清单（13 项）

- `name` 姓名（基础信息 → TBL-11 talent_base.name）
- `gender` 性别（基础信息 → TBL-11 talent_base.gender）
- `birth_date` 出生年月（基础信息 → TBL-11 talent_base.birth_date）
- `education_level` 学历（教育经历 → TBL-11 talent_base.education_level）
- `school` 院校（教育经历 → TBL-11 talent_base.school）
- `major` 专业（教育经历 → TBL-11 talent_base.major）
- `work_years` 工作年限（工作经历 → TBL-11 talent_base.work_years）
- `work_exp` 任职经历（工作经历 → TBL-12 talent_work_exp）
- `project_exp` 项目经历（工作经历 → TBL-13 talent_project_exp）
- `skill` 技能（技能与证书 → TBL-14 talent_skill）
- `cert` 证书（技能与证书 → TBL-15 talent_cert）
- `phone` 手机号（联系方式 → TBL-11 talent_base.phone_enc / phone_hash）
- `email` 邮箱（联系方式 → TBL-11 talent_base.email_enc）
