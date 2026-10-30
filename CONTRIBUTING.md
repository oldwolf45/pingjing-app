# 贡献指南

感谢你对紫苏叶策略的关注！欢迎各种形式的贡献。

## 代码贡献流程

1. **Fork 仓库** 到你的账号
2. **创建分支**：`git checkout -b feature/your-feature`
3. **提交改动**：遵循 [Conventional Commits](#提交规范) 规范
4. **跑通测试**：确保所有测试通过
5. **发起 Pull Request**：描述清楚改动点和原因

## 开发环境搭建

详见 [开发指南](docs/DEVELOPMENT.md)。

## 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/) 格式：

```
<type>(<scope>): <description>

[可选正文]

[可选脚注]
```

### type 类型

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式（不影响逻辑） |
| `refactor` | 重构（既不新增功能也不修 bug） |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具/依赖等工程化改动 |
| `ci` | CI/CD 配置 |

### scope 范围

常用：`api` / `db` / `algo` / `frontend` / `docs` / `build`

### 示例

```
feat(api): 新增个股估值接口

新增 PEG / PS 等估值指标，供详情页展示

Closes #123
```

## 测试要求

- 新增功能必须附带测试
- Bug 修复必须附回归测试
- 运行全部测试：`pytest backend/tests/`
- 算法改动必须通过金样本验证

## 代码风格

- Python：遵循 PEP 8，行宽 100 字符
- 前端：Vue 3 组合式 API，组件名 PascalCase
- 中文优先（注释、文档、提交信息），专业术语用英文

## 算法改动特别注意

⚠️ **算法核心文件（`backend/app/algo/`）有指纹校验，改动后需要更新指纹清单。**

算法改动流程：
1. 修改算法文件
2. 跑金样本验证，确认评分结果正确
3. 更新指纹清单：`python -c "from app.legacy_bridge import write_manifest; write_manifest()"`
4. 在 PR 中说明：改了什么、为什么、金样本验证结果

## 报告问题

提 Issue 时请包含：

- **环境**：Windows 版本、APP 版本
- **复现步骤**：怎么操作出现的问题
- **预期行为**：应该是什么样的
- **实际行为**：实际是什么样的
- **日志**：数据目录下 `logs/zisuye.log` 相关片段

## 功能建议

欢迎提功能建议！请说明：
- 解决什么问题
- 你的使用场景
- 大致的实现思路（如果有的话）
