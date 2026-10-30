# 打包与发布指南

> 如何将紫苏叶策略打包成 Windows 安装包并发布。

---

## 打包产物

| 产物 | 格式 | 说明 |
|------|------|------|
| 绿色目录 | onedir 文件夹 | `dist/紫苏叶/`，可直接运行，绿色免安装 |
| 安装包 | `.exe` | Inno Setup 打包，带开始菜单/桌面图标/卸载程序 |

---

## 前置准备

1. **Python 3.12** 64 位
2. **Node.js** 18+（构建前端用）
3. **PyInstaller** `pip install pyinstaller`
4. **Inno Setup** 6+（打安装包用，官网下载免费版）
5. Windows 10/11（必须在 Windows 上打 Windows 包）

---

## 打包步骤

### 第一步：构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

确认 `frontend/dist/` 目录下有 `index.html` 和静态资源。

### 第二步：PyInstaller 打包

```bash
# 在项目根目录
pyinstaller zisuye.spec
```

打包完成后，产物在 `dist/紫苏叶/` 目录。

### 第三步：验证绿色版

双击 `dist/紫苏叶/紫苏叶.exe`，确认：

- [ ] 能正常启动，3 秒内出窗口
- [ ] 数据目录在 `%LocalAppData%\ZiSuYe\`
- [ ] 仪表盘有数据（或首次导入成功）
- [ ] 更新按钮可用
- [ ] 设置页能打开数据目录

### 第四步：打 Inno Setup 安装包

```bash
# 命令行方式（需先把 iscc 加入 PATH）
iscc installer/zisuye.iss
```

或用 Inno Setup 编译器打开 `installer/zisuye.iss`，点「编译」。

安装包输出在 `installer/Output/紫苏叶-setup-vX.Y.Z.exe`。

### 第五步：安装包验收

在**干净的机器/虚拟机**上测试：

- [ ] 双击安装包，安装向导正常显示
- [ ] 安装完成后桌面有图标
- [ ] 双击图标 3 秒内出窗口
- [ ] 卸载程序能在「添加或删除程序」中找到
- [ ] 卸载后程序文件被清除，但数据目录保留

---

## 版本号管理

版本号在三个地方，发版前必须全部更新：

| 位置 | 文件 | 变量 |
|------|------|------|
| 1 | `pyproject.toml` | `version = "X.Y.Z"` |
| 2 | `installer/version_info.txt` | FileVersion / ProductVersion |
| 3 | `zisuye.spec` | 不用改，自动从 version_info.txt 读 |
| 4 | `CHANGELOG.md` | 新增版本条目 |
| 5 | `backend/app/main.py` | `APP_VERSION = "X.Y.Z"` |

**发版检查清单：**

- [ ] 所有版本号已更新且一致
- [ ] CHANGELOG.md 已补充本版本更新内容
- [ ] 全部测试通过：`pytest`
- [ ] 前端已构建：`npm run build`
- [ ] 绿色版已验证
- [ ] 安装包已在干净机器上验证
- [ ] Git 打 tag：`git tag vX.Y.Z`
- [ ] 推送到远程仓库

---

## 升级与数据安全

### 跨版本升级

1. 用户安装新版本
2. 启动时检测 `schema_version` 落后
3. 自动备份当前数据库到 `backups/pre_migration/`
4. 执行数据库迁移
5. 迁移失败自动回滚到备份

### 保证不丢数据的设计

| 机制 | 说明 |
|------|------|
| 数据目录独立 | 存在 `%LocalAppData%\ZiSuYe\`，卸载/重装都碰不到 |
| 安装目录只读 | 程序只写数据目录，不写安装目录 |
| 每日备份 | 每天首次启动自动备份，保留 7 份 |
| 每周备份 | 每周一首次启动备份，保留 8 份 |
| 升级前备份 | 每次迁移前单独备份，保留 5 份 |
| WAL 模式 | SQLite WAL + synchronous=FULL，断电不丢数据 |
| 只追加表 | 评分快照、池变动、推荐记录用触发器禁止修改删除 |
| 外送备份 | 每周自动复制到 `Documents\紫苏叶备份\` |

---

## 发布到 GitHub

### Release 附件

每个 Release 包含：

| 文件 | 说明 |
|------|------|
| `紫苏叶-setup-vX.Y.Z.exe` | Windows 安装包 |
| `紫苏叶-vX.Y.Z-portable.zip` | 绿色版 zip（可选） |
| Source code (zip) | GitHub 自动生成 |

### Release Note

基于 CHANGELOG.md 中的版本条目，包含：

- 版本号和日期
- 新增 / 修复 / 改进 三个分类
- 升级注意事项（如果有数据迁移）
- 校验和（SHA256）

---

## 常见问题

### Q: 打包后启动崩溃

常见原因：
1. **缺隐藏导入**：检查 `zisuye.spec` 的 `hiddenimports` 是否完整
2. **数据文件没打进去**：检查 `datas` 列表，特别是 `frontend/dist` 和 `schema.sql`
3. **路径问题**：打包后用 `sys._MEIPASS` 找资源，确认 `app/paths.py` 逻辑正确

### Q: 杀毒软件报毒

PyInstaller 打包的程序容易被误报，缓解方法：

1. 用 onedir 模式而非 onefile
2. 关闭 UPX 压缩（已关）
3. 加完整的版本信息（`version_info.txt`）
4. 加数字代码签名（需要购买证书）
5. 提交给各大杀毒厂商白名单

### Q: 体积太大

当前体积应该在 50~80MB（装好）。如果过大，检查：

1. `excludes` 列表是否排除了 pandas/numpy/PIL 等大包
2. 前端 `node_modules` 有没有被打进去（应该只读 `frontend/dist`）
3. 测试代码有没有被打进去
