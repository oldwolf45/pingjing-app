# 发布前检查清单

每次发版前过一遍这个清单，确保质量。

## 代码质量

- [ ] 所有测试通过：`pytest backend/tests/`
- [ ] 前端构建成功：`cd frontend && npm run build`
- [ ] 没有遗留的 `print()` / `console.log()` / `debugger`
- [ ] 没有硬编码的密钥、Token、敏感路径

## 版本号

- [ ] `pyproject.toml` 版本号已更新
- [ ] `installer/version_info.txt` 版本号已更新
- [ ] `installer/zisuye.iss` 版本号已更新
- [ ] `CHANGELOG.md` 已更新当前版本的变更记录
- [ ] Git tag 已创建：`vX.Y.Z`

> 提示：用 `python scripts/bump_version.py X.Y.Z` 一键同步版本号

## 打包验证

- [ ] PyInstaller 打包成功，exe 能启动
- [ ] 启动后不报错，首页正常加载
- [ ] 核心功能可用（导入数据、评分、池子）
- [ ] 数据目录正确（`%LOCALAPPDATA%\ZiSuYe`）
- [ ] 卸载再重装数据不丢失

## 文档检查

- [ ] README 最新
- [ ] CHANGELOG 完整
- [ ] 部署指南对应最新打包方式
- [ ] 已知问题已记录

## 安全检查

- [ ] `.gitignore` 中没有遗漏的敏感文件
- [ ] 依赖没有已知高危漏洞（`pip-audit` / `npm audit`）
- [ ] 没有意外提交的大文件、测试数据

## License 合规

- [ ] 所有第三方依赖的 License 兼容 MIT
- [ ] 第三方代码/资源已标注来源
- [ ] 项目根目录有 LICENSE 文件

## Git 状态

- [ ] main 分支干净，没有未提交的改动
- [ ] 最后一个 commit 的 CI 通过
- [ ] tag 指向正确的 commit
