# AWS Certified Cloud Practitioner (CLF-C02) 知识点总结

> 按课程章节整理的复习模版，学习过程中在对应章节下方填写要点。

---

## Section 4: IAM - Identity and Access Management

- **AWS IAM** — 身份与访问管理服务，控制谁可以对哪些 AWS 资源与 API 执行哪些操作（用户、组、角色、策略）。

### 身份（Who）与策略（What）的关系

- **IAM Principals（主体）** — 可以请求对 AWS 资源执行操作的实体，即"谁在发起请求"。
  - **Principal 类型**：
    - **AWS 账户（根用户）**：`"Principal": {"AWS": "arn:aws:iam::123456789012:root"}`
    - **IAM 用户**：`"Principal": {"AWS": "arn:aws:iam::123456789012:user/username"}`
    - **IAM 角色**：`"Principal": {"AWS": "arn:aws:iam::123456789012:role/rolename"}`
    - **AWS 服务**：`"Principal": {"Service": "ec2.amazonaws.com"}`（如 EC2、Lambda）
    - **联合身份用户**：通过 SAML、OIDC、Cognito 等联合的外部用户
    - **匿名用户（公开访问）**：`"Principal": "*"`
  - **使用场景**：在**资源策略**（如 S3 Bucket Policy、SNS Topic Policy）和**信任策略**（Role Trust Policy）中指定谁可以访问资源或担任角色。
  - **考点**：Principal 出现在资源策略中，不出现在身份策略（Identity-based Policy）中；身份策略隐式以附加的身份为 Principal。

- **IAM policy（策略）** — 定义「能做什么」的 JSON 文档，是权限的载体。
  - 核心元素：**Effect**（Allow/Deny）、**Action**（如 `s3:GetObject`）、**Resource**（如某个桶/对象）、可选 **Condition**（IP、时间、MFA 等）。
  - 策略不单独存在，必须**附加到**某个身份或资源上才会生效。
  - **策略类型**：
    - **AWS 托管策略 (AWS Managed Policies)**：AWS 预定义、维护，适合常见用例（如 `ReadOnlyAccess`、`PowerUserAccess`）。
    - **客户托管策略 (Customer Managed Policies)**：用户自定义、可复用，可挂到多个身份上，便于集中管理与版本控制。
    - **内联策略 (Inline Policies)**：嵌入单一用户/角色/组，随主体删除而消失，适合一对一的特殊权限。
  - **身份策略 vs 资源策略**：
    - **身份策略 (Identity-based)**：附加到 IAM 用户/组/角色，定义该身份能做什么。
    - **资源策略 (Resource-based)**：附加到资源（如 S3 桶、SQS 队列），定义谁可以访问该资源，包含 Principal 字段。
  - **权限边界 (Permissions Boundary)**：限制 IAM 实体的最大权限，身份策略授予的权限不能超出边界。
  - **考点**：最终权限 = 身份策略 ∩ 资源策略 ∩ 权限边界 ∩ SCP（Organizations）；显式 Deny 优先于任何 Allow。

- **IAM users（用户）** — 代表「一个人或一个应用」的**长期身份**，有固定凭证。
  - 凭证：登录控制台用**密码**，API/CLI 用**访问密钥**（Access Key + Secret）。
  - 权限来源：直接附加到用户上的策略 + 用户所属**组**上的策略（两者叠加）。
  - 适用：需要长期、可追溯的「谁在操作」时用用户；应用尽量用 **role** 而非把 Access Key 写在代码里。

- **IAM user groups（用户组）** — 只做「归类」，**组本身不能登录**，没有凭证。
  - 把多个 IAM 用户放进一个组，把策略挂在**组**上，组内所有用户自动获得该组权限。
  - 便于批量授权、按角色（如开发组、运维组）管理，用户离职时从组移除即可收回权限。

- **IAM roles（角色）** — **没有长期凭证**的身份，可被「担任」（AssumeRole），获得**临时安全凭证**。
  - 谁可以担任：本账号的 IAM 用户/组、其他 AWS 账号、AWS 服务（如 EC2、Lambda）、外部 IdP（联合身份）。
  - 典型用法：EC2 实例挂 IAM 角色访问 S3，无需在机器上存 Access Key；跨账号访问；Lambda 访问其他服务。
  - 权限来源：附加到角色上的策略；还可配置**信任策略**（Trust Policy）规定「谁可以担任这个角色」。

**小结**：Policy 定义权限；User 是长期身份、可挂策略或通过 Group 继承；Group 是用户的集合、在组上挂策略统一授权；Role 是临时身份、由用户或服务担任，适合跨账号与服务间访问。

**考点**：IAM 用户使用 **AWS CLI** 与 AWS 服务交互时，必须提供 **Access keys**（Access Key ID + Secret Access Key）；控制台才用用户名+密码。CLI/API 不用 username/password 认证。

<!-- 填写本节知识点 -->

---

## Section 5: EC2 - Elastic Compute Cloud

- **Amazon EC2 (Elastic Compute Cloud)** — 在云中提供可扩展的虚拟服务器（实例），按需启动、配置与管理，支持多种实例类型、镜像（AMI）与购买选项。
- **AMI (Amazon Machine Image)** — EC2 的“模板”：包含操作系统、应用与配置，从一个 AMI 可启动多台实例，相当于本地“从单一模板创建多台虚拟服务器”。EBS Snapshot 只是块存储的副本，不是整机模板。

### EC2 购买与容量选项（按计费/租用方式）

- **On-Demand Instances（按需实例）**  
  按实际使用时长计费，无长期承诺、无预付。适合短期、不可预测或测试流量。单价最高，但灵活。

- **Reserved Instances（预留实例）**  
  承诺 1 年或 3 年使用，换取明显折扣（最高可达 72%）。适合稳定、可预测的基线负载。
  - **三种付款选项（Upfront Options）**：
    | 付款选项 | 预付金额 | 月费 | 折扣力度 |
    |---------|---------|------|---------|
    | **All Upfront（全预付）** | 全部预付 | 无 | **最大折扣** |
    | **Partial Upfront（部分预付）** | 部分预付 | 有月费 | 中等折扣 |
    | **No Upfront（无预付）** | 无 | 全部按月 | 最小折扣 |
  - **承诺期限**：1 年 或 3 年（3 年折扣更大）。
  - **折扣规律**：预付越多 + 期限越长 = 折扣越大。**All Upfront + 3 年** = 最大折扣组合。
  - **预留范围**：
    - **Zonal RI**：绑定特定可用区，保证容量预留。
    - **Regional RI**：区域级，不保证容量但更灵活（可用于区域内任意 AZ，可自动应用于匹配的实例大小）。
  - **考点**：需要"最大折扣 + 稳定负载"选 **All Upfront + 3 年 RI**；不想大额预付选 **No Upfront RI** 或 **Savings Plans**。

- **Spot Instances（竞价实例）**  
  使用 AWS 闲置容量，价格随供需浮动，通常远低于按需价。可能被**中断**（两分钟通知），适合可容错、可批处理、可弹性伸缩的工作负载（如批处理、大数据、部分无状态 Web）。

- **Dedicated Instances（专用实例）**  
  运行在**专供你的账户使用的物理主机**上，与其它客户在硬件上隔离；实例在重启/停止再启动后可能在不同专用主机上。适合合规、许可或策略要求“单租户物理机”的场景。  
  （与 **Dedicated Hosts** 区分：Dedicated Host 是整台物理机可见、可绑许可，实例放置更可控。）

**简要对比**：按需 = 灵活价高；预留 = 承诺换折扣；Spot = 低价但可能被回收；专用 = 物理隔离、合规/许可场景。

<!-- 填写本节知识点 -->

---

## Section 6: EC2 Instance Storage

- **Amazon EBS (Elastic Block Store)** — 块级存储，挂载到单台 EC2 作为块设备；支持 gp3、io2 等卷类型、快照与加密，数据持久化独立于实例生命周期。
- **Amazon EFS (Elastic File System)** — 托管 NFS 文件系统，多台 EC2（含跨 AZ）可同时挂载、共享文件，按用量扩展，适合共享存储、CI/CD、内容管理。

<!-- 填写本节知识点 -->

---

## Section 7: ELB & ASG - Elastic Load Balancing & Auto Scaling Groups

- **Elastic Load Balancer (ELB)** — 将流量分发到多台目标（如 EC2），可跨多可用区；自动剔除不健康目标，由健康实例承接流量，提升可用性与容错。
- **Amazon EC2 Auto Scaling** — 按策略维持或调整实例数量（最小/最大/期望容量），在实例故障或负载变化时自动扩容/缩容或替换实例，与 ELB 配合实现高可用。

**考点**：维护高可用与容错架构时，常考的两个服务是 **Elastic Load Balancer** 和 **Amazon EC2 Auto Scaling**：ELB 负责流量分发与健康检查；ASG 负责维持足够健康实例并替换故障实例。CloudFormation、Network ACLs、Direct Connect 不直接提供此类 HA/容错能力。

<!-- 填写本节知识点 -->

---

## Section 8: Amazon S3

- **Amazon S3** — 对象存储，按桶与对象组织，适合海量对象、静态资源、备份、大数据等；多存储类可选（见下表）。

### S3 存储类（Storage Class）概览

按访问频率、可用性要求和成本从高到低排列：

| 存储类                            | 访问频率 / 场景                  | 可用性 / 取回                               | 成本                                      |
| --------------------------------- | -------------------------------- | ------------------------------------------- | ----------------------------------------- |
| **S3 Standard**                   | 频繁访问                         | 多 AZ，低延迟                               | 最高                                      |
| **S3 Intelligent-Tiering**        | 访问模式未知或会变化             | 自动在多个层间移动，低延迟                  | 按实际层计费，无取回费                    |
| **S3 Standard-IA**                | 不常访问，但需快速取回           | 多 AZ，低延迟                               | 低于 Standard，有取回费                   |
| **S3 One Zone-IA**                | 不常访问，可接受单 AZ 丢失       | 单 AZ，低延迟                               | 比 Standard-IA 更低，有取回费             |
| **S3 Glacier Flexible Retrieval** | 归档、合规、长期分析数据         | 取回需分钟～小时（Expedited/Standard/Bulk） | 低                                        |
| **S3 Glacier Deep Archive**       | 长期归档，极少取回（如 7–10 年） | 取回需小时级                                | 最低                                      |
| **S3 Glacier Instant Retrieval**  | 归档但偶尔需毫秒级取回           | 取回为毫秒级                                | 介于 Glacier Flexible 与 Standard-IA 之间 |

**简要记忆：**

- **Standard** — 常用数据，多 AZ，价高。
- **Intelligent-Tiering** — 不确定访问模式时用，自动降层省成本。
- **Standard-IA / One Zone-IA** — 不常访问，要低延迟取回；One Zone 更便宜但只单 AZ。
- **Glacier Flexible Retrieval** — 适合 **active archives**、**long-term analytic data**；取回有延迟。
- **Glacier Deep Archive** — 极少取回的长期归档，成本最低。
- **Glacier Instant Retrieval** — 归档 + 偶尔需要即时取回。

<!-- 填写本节知识点 -->

---

## Section 9: Databases & Analytics

- **Amazon RDS (Relational Database Service)** — 托管关系型数据库，支持 MySQL、PostgreSQL、MariaDB、Oracle、SQL Server；自动备份、补丁、多 AZ 可选，适合传统 OLTP 应用。
- **Amazon Aurora** — AWS 托管的关系型数据库，兼容 MySQL/PostgreSQL，高可用、自动扩展存储、多 AZ 复制，适合对可用性与性能要求高的 OLTP 场景。
- **Amazon DynamoDB** — 全托管 NoSQL 键值/文档数据库，低延迟、自动扩展，按请求或容量计费，适合高并发、可变工作负载与 Serverless 应用。
- **Amazon Redshift** — 托管数据仓库，面向分析型负载（OLAP），列式存储、大规模并行查询，适合 BI、报表与大数据分析。
- **Amazon Neptune** — 托管图数据库，支持属性图与 RDF，适合关系与连接复杂的场景（如社交、推荐、知识图谱、欺诈检测）。
- **Amazon ElastiCache** — 托管内存缓存，兼容 Redis 与 Memcached，用于减轻数据库压力、会话存储、实时排行榜等低延迟读写场景。
- **Amazon EMR (Elastic MapReduce)** — 托管大数据处理集群，运行 Hadoop、Spark、Hive、Presto 等，用于大规模数据处理与分析。
- **Amazon Kinesis** — 实时流数据处理：Data Streams（自定义消费、重放）、Data Firehose（投递到 S3/Redshift 等）、Data Analytics（SQL 分析流数据），用于日志、点击流、IoT 等实时摄取与分析。
- **AWS Database Migration Service (DMS)** — 将数据库迁入 AWS 的服务，支持同构（如 MySQL→RDS MySQL）与异构（如 Oracle→Aurora）迁移，可在迁移期间保持源库可用、持续同步。
- **AWS Glue** — 托管 ETL 与数据目录：发现、编目、转换数据（如 S3、RDS），为 Athena、Redshift、EMR 等提供元数据，无服务器、按任务计费。
- **Amazon Athena** — 无服务器交互式查询服务，使用标准 SQL 直接分析 S3 中的数据。
 

<!-- 填写本节知识点 -->

---

## Section 10: Other Compute Services (ECS, Lambda, Batch, Lightsail)

- **Amazon ECS (Elastic Container Service)** — 托管容器编排，运行 Docker 容器；支持 EC2 模式（自管节点）与 Fargate 模式（无服务器），与 ECR、ALB、CloudMap 等集成，适合微服务与容器化应用。
- **AWS Fargate** — 无服务器容器计算引擎，用于 ECS 和 EKS，无需管理 EC2 实例。
  - **核心特点**：
    - **无服务器**：无需预置、管理、扩展集群 VM；AWS 自动管理底层基础设施。
    - **按任务/Pod 计费**：按 vCPU 和内存的实际使用量计费（按秒），无需为闲置容量付费。
    - **隔离性**：每个任务/Pod 运行在自己的内核中，天然的任务级隔离。
  - **与 EC2 模式对比**：
    - **Fargate**：无需管理实例、自动扩展、按任务付费；适合不想管理集群、负载波动大的场景。
    - **EC2 模式**：更多控制（实例类型、GPU、自定义 AMI）、可使用 Spot/RI 省钱；适合稳定负载或有特殊需求。
  - **支持服务**：Amazon ECS、Amazon EKS、AWS Batch。
  - **典型用例**：微服务、批处理、CI/CD 任务、无状态 Web 应用。
  - **考点**：需要"无服务器运行容器、无需管理 EC2"时选 **Fargate**；需要"事件驱动的短函数"选 **Lambda**；需要"完全控制底层实例"选 **ECS/EKS on EC2**。
- **AWS Lambda** — 无服务器函数，按事件触发、按执行时长计费，无需管理服务器；适合事件驱动、API 后端、定时任务等。
- **AWS Batch** — 托管批处理作业调度，在 EC2 或 Fargate 上运行大规模批处理任务，适合 HPC、数据处理流水线。
- **Amazon Lightsail** — 轻量级 VPS：固定月费、简单控制台，适合小站、原型与入门场景。

<!-- 填写本节知识点 -->

---

## Section 11: Deployments & Managing Infrastructure at Scale

- **AWS CloudFormation** — 基础设施即代码（IaC）：用模板（YAML/JSON）定义一组 AWS 资源，一键部署、更新与删除（栈），便于版本控制与环境一致性。
- **Amazon Elastic Beanstalk** — PaaS：上传应用代码（如 Java、Node、Python、Docker），自动配置并管理底层 EC2、ELB、ASG、监控等，适合快速部署 Web 应用、无需手管基础设施。
  - **核心特点**：开发者只需关注代码，Beanstalk 自动处理容量配置、负载均衡、自动扩展、健康监控。
  - **支持平台**：Java、.NET、PHP、Node.js、Python、Ruby、Go、Docker。
  - **环境类型**：Web Server Environment（处理 HTTP 请求）、Worker Environment（处理后台任务/SQS 消息）。
  - **部署策略**：All at Once（最快但有停机）、Rolling（分批更新）、Rolling with Additional Batch（保持满容量）、Immutable（新建全部实例）、Blue/Green（使用 Route 53 或交换 URL）。
  - **与 CloudFormation 区别**：Beanstalk 是"代码到应用"的 PaaS，CloudFormation 是"模板到基础设施"的 IaC；Beanstalk 底层实际使用 CloudFormation 创建资源。
  - **考点**：需要"快速部署应用、无需管理基础设施"时选 Elastic Beanstalk；需要"完全控制底层资源定义"时选 CloudFormation。
- **AWS Systems Manager (SSM)** — 在规模上管理 EC2 与混合环境（本地服务器）：补丁、配置、运行命令、清单、Session Manager 免 SSH 登录等，统一运维与合规。
  - **核心功能**：
    - **Run Command**：远程批量执行命令，无需 SSH/RDP。
    - **Patch Manager**：自动化补丁管理，定义基线与合规策略。
    - **Session Manager**：通过浏览器或 CLI 安全登录实例，无需开放 SSH 端口、无需管理密钥。
    - **Parameter Store**：安全存储配置数据与密钥（明文或 KMS 加密），可版本管理与审计。
    - **State Manager**：定义与维护实例的期望配置状态。
    - **Inventory**：收集实例元数据（软件、配置、网络等）用于合规审计。
    - **Automation**：自动化常见运维任务（如打补丁、创建 AMI、修复漂移）。
  - **SSM Agent**：需在实例上安装 SSM Agent 才能被 Systems Manager 管理（Amazon Linux/Ubuntu AMI 默认预装）。
  - **混合环境**：可管理本地服务器、其他云上的 VM，只需安装 SSM Agent 并配置 IAM 角色。
  - **考点**：需要"无需 SSH/堡垒机安全登录 EC2"选 **Session Manager**；需要"集中管理补丁"选 **Patch Manager**；需要"存储应用配置/密钥"选 **Parameter Store**（或 Secrets Manager）。
- **AWS OpsWorks** — 基于 Chef/Puppet 的配置管理与自动化，用于部署、配置、运维 EC2 等资源（如应用栈、层、实例），适合需要脚本化运维的场景。
- **AWS Server Migration Service (SMS)** — 将本地虚拟机（VM）批量迁移到 AWS（如迁到 EC2），支持增量复制与自动化调度。
- **AWS Application Discovery Service** — 发现并收集本地服务器、应用与依赖关系，用于迁移前的评估与规划（如配合 SMS、DMS、Migration Hub）。
- **AWS CodePipeline** — 托管 CI/CD 流水线：从源码（CodeCommit/GitHub 等）到构建（CodeBuild）、测试、部署的自动化流水线，可对接 Lambda、ECS、CloudFormation 等。
- **AWS CodeCommit** — 托管 Git 代码仓库服务，安全、可扩展、与 AWS 深度集成。
  - **核心特点**：
    - **完全托管 Git**：标准 Git 操作，无需管理服务器。
    - **与 AWS 集成**：IAM 权限控制、CloudWatch 监控、EventBridge 触发事件、CodePipeline 源阶段。
    - **安全**：静态加密、传输加密、IAM 细粒度访问控制。
    - **无仓库大小限制**：适合大型代码库。
  - **典型用例**：企业代码托管、CI/CD 源码管理、需要 AWS 生态集成的项目。
  - **考点**：需要"AWS 托管的 Git 仓库"时选 **CodeCommit**；外部托管选 GitHub/GitLab。
- **AWS CodeBuild** — 完全托管的持续集成服务，编译代码、运行测试、生成构建产物。
  - **核心特点**：
    - **无服务器**：按构建分钟计费，无需管理构建服务器。
    - **可扩展**：自动扩展，支持并行构建。
    - **预置环境**：提供多种预配置环境（Java、Python、Node.js、Go、Docker 等），也支持自定义镜像。
    - **buildspec.yml**：定义构建步骤（install、pre_build、build、post_build）、环境变量、产物输出。
  - **典型用例**：编译打包、运行单元测试、构建 Docker 镜像、生成部署包。
  - **与 CodePipeline 配合**：CodePipeline 编排流程，CodeBuild 负责"构建"阶段。
  - **考点**：需要"托管构建服务、编译代码"时选 **CodeBuild**；与 Jenkins 类似但无需管理服务器。
- **AWS CodeDeploy** — 托管部署服务，自动化将应用部署到 EC2、Lambda、ECS 或本地服务器。
  - **核心功能**：
    - **自动化部署**：从 S3 或 GitHub 拉取应用，按配置部署到目标。
    - **部署策略**：
      - **In-Place（就地更新）**：逐台或分批更新现有实例（仅 EC2/本地）。
      - **Blue/Green**：创建新环境，验证后切换流量，旧环境可回滚。
    - **回滚**：部署失败自动回滚，或手动回滚到之前版本。
    - **钩子脚本（Hooks）**：在部署生命周期各阶段运行自定义脚本（如 BeforeInstall、AfterInstall、ApplicationStart）。
  - **支持平台**：EC2、Lambda、ECS、本地服务器（需安装 CodeDeploy Agent）。
  - **与 CodePipeline 配合**：CodePipeline 编排整个 CI/CD 流程，CodeDeploy 负责"部署"阶段。
  - **考点**：需要"自动化部署到 EC2/Lambda/ECS"或"Blue/Green 部署"时选 **CodeDeploy**；完整 CI/CD 流水线选 **CodePipeline**；PaaS 一键部署选 **Elastic Beanstalk**。
- **AWS CodeStar** — 统一界面快速开发、构建、部署 AWS 应用，整合所有 Code* 服务。
  - **核心功能**：
    - **项目模板**：提供预配置模板（Web 应用、Lambda、Alexa Skill 等），一键创建完整项目。
    - **统一仪表盘**：集中管理代码（CodeCommit）、构建（CodeBuild）、部署（CodeDeploy/Elastic Beanstalk）、流水线（CodePipeline）。
    - **团队协作**：集成 IAM 管理团队成员权限，支持 JIRA 集成。
    - **IDE 集成**：与 Cloud9、VS Code、Eclipse 等 IDE 集成。
  - **典型用例**：快速启动新项目、团队协作开发、学习 AWS 开发工具链。
  - **考点**：需要"一站式快速搭建 CI/CD 项目"或"统一管理 Code* 服务"时选 **CodeStar**。

### AWS 开发者工具（Code 系列）汇总

| 服务 | 功能 | 类比 |
|------|------|------|
| **CodeCommit** | Git 代码仓库 | GitHub/GitLab |
| **CodeBuild** | 构建与测试 | Jenkins |
| **CodeDeploy** | 部署到 EC2/Lambda/ECS | Ansible/Octopus |
| **CodePipeline** | CI/CD 流水线编排 | Jenkins Pipeline |
| **CodeStar** | 统一项目管理界面 | — |

- **AWS Amplify** — 全栈 Web 与移动应用开发平台，提供前端托管、后端服务与 CI/CD。
  - **Amplify Hosting**：托管静态网站与 SSR（服务端渲染）应用，与 Git 集成实现自动构建与部署，支持 React、Vue、Angular、Next.js 等框架，自带 CDN（CloudFront）、HTTPS、自定义域名。
  - **Amplify Studio**：可视化界面设计与开发，Figma 到 React 代码转换，无需手写全部代码。
  - **Amplify Libraries**：前端 SDK（JS、iOS、Android、Flutter），简化与 AWS 服务（Cognito 认证、AppSync GraphQL、S3 存储等）的集成。
  - **Amplify CLI**：命令行工具，用于配置后端资源（Auth、API、Storage、Functions 等），底层使用 CloudFormation 部署。
  - **典型用例**：快速搭建全栈 Web/移动应用、单页应用托管、Jamstack 站点、原型与 MVP 开发。
  - **与 Elastic Beanstalk 区别**：Amplify 专注**前端 + Serverless 后端**，更适合现代 Web/移动开发；Beanstalk 是通用 PaaS，更适合传统服务端应用（如 Java、.NET、PHP）。
  - **考点**：需要"快速托管静态网站或 SPA 并自动 CI/CD"或"全栈 Serverless 移动/Web 应用开发"时选 **AWS Amplify**。

<!-- 填写本节知识点 -->

---

## Section 12: Leveraging the AWS Global Infrastructure

- **Amazon CloudFront** — CDN（内容分发网络），通过全球**边缘站点（Edge Locations）** 缓存并就近分发静态/动态内容，降低延迟、减轻源站压力。
  - **核心概念**：
    - **Edge Locations（边缘站点）**：全球 400+ 个节点，缓存内容，用户请求就近响应。
    - **Regional Edge Caches**：区域边缘缓存，介于边缘站点与源站之间，缓存较少访问的内容。
    - **Origin（源站）**：内容的原始来源，可为 S3、EC2、ELB、API Gateway 或任意 HTTP 服务器。
    - **Distribution（分发）**：CloudFront 的配置单元，定义源站、缓存行为、域名等。
  - **主要功能**：
    - **静态内容加速**：图片、CSS、JS、视频等缓存到边缘。
    - **动态内容加速**：API 响应等通过 AWS 骨干网优化路径，减少延迟。
    - **HTTPS**：与 ACM 集成提供免费 SSL/TLS 证书，支持 SNI。
    - **地理限制 (Geo Restriction)**：按国家/地区允许或阻止访问。
    - **签名 URL / 签名 Cookie**：保护私有内容，限制访问时间或用户。
    - **Lambda@Edge / CloudFront Functions**：在边缘运行代码，自定义请求/响应（如 A/B 测试、URL 重写、认证）。
    - **Origin Shield**：额外缓存层，减少源站负载，提高缓存命中率。
  - **典型用例**：静态网站加速、视频点播/直播、API 加速、软件分发。
  - **CloudFront vs Global Accelerator**：
    | 特性 | CloudFront | Global Accelerator |
    |------|------------|-------------------|
    | 类型 | CDN（内容缓存） | 网络加速（无缓存） |
    | 适用 | 可缓存内容（静态/动态） | TCP/UDP 应用、非 HTTP |
    | IP | 动态（域名解析） | 固定任播 IP |
    | 边缘处理 | Lambda@Edge | 无 |
  - **考点**：需要"缓存静态内容、降低延迟"或"全球分发网站/API"时选 **CloudFront**；需要"固定 IP 入口"或"非 HTTP 协议加速"选 **Global Accelerator**。

- **AWS Global Accelerator** — 使用 AWS 全球网络与固定任播 IP，将流量经最优路径路由到多区域端点（如 ALB、EC2、NLB），提升可用性与性能，适合需稳定入口 IP 或跨区域高可用的应用。

- **AWS Outposts** — 将 AWS 基础设施、服务、API 扩展到客户本地数据中心或边缘站点。

<!-- 填写本节知识点 -->

---

## Section 13: Cloud Integrations

- **AWS Storage Gateway** — 混合存储：在本地部署网关，将本地数据以文件、卷或磁带形式与 AWS 存储对接，便于备份、迁移与分层。
  - **三种网关类型**：
    - **S3 File Gateway**：本地通过 NFS/SMB 协议挂载，数据以对象形式存入 S3；本地缓存热数据，冷数据在 S3，适合文件共享、备份、数据湖摄取。
    - **FSx File Gateway**：本地通过 SMB 访问 Amazon FSx for Windows File Server，低延迟访问云端 Windows 文件共享，适合 Windows 应用与用户主目录。
    - **Volume Gateway**：本地通过 iSCSI 挂载块存储卷，数据异步备份到 S3（以 EBS 快照形式）。
      - **Cached Volumes**：主数据在 S3，本地缓存热数据，适合大容量但本地存储有限。
      - **Stored Volumes**：主数据在本地，异步快照到 S3，适合低延迟访问完整数据集。
    - **Tape Gateway（VTL）**：模拟物理磁带库，本地备份软件通过 iSCSI 写虚拟磁带，数据存入 S3/Glacier，适合替代传统磁带备份。
  - **部署形式**：可在本地 VMware/Hyper-V/KVM 上部署虚拟机，或使用 AWS 提供的硬件设备。
  - **典型用例**：本地数据备份到云、灾难恢复、数据迁移、分层存储、磁带替代。
  - **考点**：需要"本地应用透明访问云存储"或"将本地备份/磁带迁移到 AWS"时选 **AWS Storage Gateway**；需要"物理设备搬运 PB 级数据"选 **Snowball**。
- **Amazon SQS (Simple Queue Service)** — 托管消息队列，解耦组件：消息持久化在队列中直到被成功处理并删除，若消费方故障则消息保留不丢，适合异步、削峰、可靠传递。**考点**：确保“组件间消息在组件故障时不丢失”应使用 **Amazon SQS**。
- **Amazon SNS (Simple Notification Service)** — 托管发布/订阅与推送：主题（Topic）发布消息，多个订阅者（HTTP/SQS/ Lambda/邮件/SMS 等）接收，适合事件通知、告警、扇出。
- **Amazon EventBridge** — 无服务器事件总线，连接应用、AWS 服务与 SaaS，实现事件驱动架构。
 
- **对比本题其他选项**：**AWS Direct Connect** 为混合云专线连接，与消息持久化无关；**Amazon SES** 为邮件收发，非组件间消息队列；**Amazon Connect** 为云联络中心（客服、IVR），非应用消息队列。

<!-- 填写本节知识点 -->

---

## Section 14: Cloud Monitoring

- **Amazon CloudWatch** — AWS 的统一监控与可观测性服务，收集指标、日志、事件，并据此告警与自动化响应。
  - **核心组件**：
    - **CloudWatch Metrics**：收集 AWS 资源与应用的时序指标（如 EC2 CPU、RDS 连接数、自定义指标）。
      - **标准指标**：AWS 服务自动发送（免费，1 分钟或 5 分钟粒度）。
      - **自定义指标**：应用通过 API/SDK 发送业务指标。
      - **高分辨率指标**：最细可达 1 秒粒度（额外付费）。
    - **CloudWatch Alarms**：基于指标阈值触发告警，可通知（SNS）或自动执行操作（如 Auto Scaling、EC2 恢复）。
      - 状态：OK / ALARM / INSUFFICIENT_DATA。
    - **CloudWatch Logs**：收集、存储、分析日志（EC2、Lambda、ECS、应用等）。
      - **Log Groups / Log Streams**：按应用/实例组织日志。
      - **Metric Filters**：从日志中提取指标（如错误计数）。
      - **Logs Insights**：交互式日志查询与分析。
      - **日志保留**：可设置 1 天～永久保留。
    - **CloudWatch Events / EventBridge**：响应 AWS 资源状态变化或定时事件，触发 Lambda、SNS、Step Functions 等。
    - **CloudWatch Dashboards**：可视化仪表盘，集中展示多服务指标。
    - **CloudWatch Agent**：安装在 EC2/本地服务器上，收集 OS 级指标（内存、磁盘）与日志。
  - **典型用例**：
    - 监控 EC2 CPU，超阈值时触发 Auto Scaling 扩容
    - 收集应用日志，设置错误告警
    - 创建仪表盘，统一查看多服务健康状态
  - **CloudWatch vs CloudTrail vs X-Ray**：
    | 服务 | 功能 | 关键词 |
    |------|------|--------|
    | **CloudWatch** | 指标、日志、告警、仪表盘 | 性能监控、资源利用率 |
    | **CloudTrail** | API 调用审计日志 | 谁做了什么操作、合规审计 |
    | **X-Ray** | 分布式追踪、调用链分析 | 应用调试、延迟分析 |
  - **考点**：需要"监控 EC2 CPU/内存"或"基于指标告警"或"收集应用日志"时选 **CloudWatch**；需要"审计 API 操作"选 **CloudTrail**；需要"追踪请求调用链"选 **X-Ray**。
- **AWS CloudTrail** — API 调用审计与日志，记录谁在何时对何资源做了何种操作，用于合规、变更审计与故障排查；与混合云连通性无关。
- **AWS X-Ray** — 分布式追踪服务，分析请求在多个服务间的调用链与延迟，用于应用调试与性能分析，**不**用于资源变更审计。
- **考点（变更管理）**：审计与监控 AWS 环境中**资源变更**的两个服务是 **AWS Config**（配置历史与合规）和 **AWS CloudTrail**（API 操作日志）。Comprehend、Transit Gateway、X-Ray 不负责变更审计。

<!-- 填写本节知识点 -->

---

## Section 15: VPC & Networking

- **AWS Direct Connect** — 从本地数据中心到 AWS 的**专用物理连接**（不经过公网），低延迟、稳定带宽，适合混合云中需要可预测性能与大量数据传输的场景。
- **AWS VPN** — 通过互联网在本地网络与 VPC 之间建立加密隧道（如 Site-to-Site VPN），无需专线、部署快，适合混合云中“先连上”或带宽要求不极高的场景。
- **AWS Transit Gateway** — 区域级网络枢纽，用于连接多个 VPC 及本地网络（VPN/Direct Connect），集中路由。
  - **核心特点**：
    - **Hub-and-Spoke 架构**：Transit Gateway 作为中心枢纽，所有 VPC 和本地网络连接到它，无需两两互联。
    - **简化网络拓扑**：N 个 VPC 只需 N 条连接（到 TGW），而非 N×(N-1)/2 条 VPC Peering。
    - **支持跨区域**：Transit Gateway Peering 可连接不同 Region 的 TGW。
    - **路由表**：可配置多个路由表实现网络分段（如生产/开发隔离）。
  - **典型用例**：大规模多 VPC 架构、混合云统一出口、共享服务 VPC、网络分段。
  - **考点**：需要"连接多个 VPC 并简化管理"或"统一混合云路由"时选 **Transit Gateway**；少量 VPC 互联可用 **VPC Peering**。

- **VPC Peering** — 两个 VPC 之间的私有网络连接，流量通过 AWS 骨干网，不经公网。
  - **核心特点**：
    - **一对一连接**：每个 Peering 只连接两个 VPC，不可传递（A↔B、B↔C 不等于 A↔C）。
    - **跨账户/跨区域**：支持不同账户或不同 Region 的 VPC 互联。
    - **无带宽瓶颈**：流量直接走 AWS 网络，无单点限制。
    - **CIDR 不能重叠**：两个 VPC 的 IP 范围不能冲突。
  - **典型用例**：少量 VPC 互联、跨账户资源共享、简单的多 VPC 架构。
  - **VPC Peering vs Transit Gateway**：
    | 场景 | 推荐 |
    |------|------|
    | 2-3 个 VPC 互联 | VPC Peering（简单、免费） |
    | 多个 VPC + 本地网络 | Transit Gateway（集中管理） |
    | 需要传递路由 | Transit Gateway |
  - **考点**：VPC Peering **不支持传递路由**；需要 A↔C 必须单独建立 Peering 或使用 Transit Gateway。

- **AWS PrivateLink** — 通过私有 IP 安全访问 AWS 服务或第三方 SaaS，流量不经公网。
  - **核心概念**：
    - **VPC Endpoint (Interface)**：在 VPC 中创建 ENI（弹性网络接口），通过私有 IP 访问目标服务。
    - **Endpoint Service**：服务提供方（如 SaaS 厂商）将 NLB 暴露为 PrivateLink 服务，消费方通过 Interface Endpoint 访问。
  - **两种 VPC Endpoint**：
    | 类型 | 实现 | 支持的服务 | 费用 |
    |------|------|------------|------|
    | **Interface Endpoint (PrivateLink)** | ENI + 私有 IP | 大多数 AWS 服务、第三方 SaaS | 按小时 + 数据量 |
    | **Gateway Endpoint** | 路由表条目 | 仅 S3、DynamoDB | 免费 |
  - **典型用例**：
    - 私有子网 EC2 访问 S3/DynamoDB（Gateway Endpoint）
    - 私有访问 AWS API（如 EC2、SSM、KMS）而不走 NAT/IGW
    - SaaS 厂商向客户 VPC 提供私有服务
  - **考点**：需要"私有访问 AWS 服务、不经过公网"时选 **VPC Endpoint**；S3/DynamoDB 用 **Gateway Endpoint**（免费）；其他服务用 **Interface Endpoint (PrivateLink)**。

- **考点（混合云连通性）**：构建混合云架构时，常用的两种**连通性**选项是 **AWS Direct Connect** 和 **AWS VPN**。Artifact、Cloud9、CloudTrail 与网络连通无关。

<!-- 填写本节知识点 -->

---

## Section 16: Security & Compliance

- **AWS 共享责任模型 (Shared Responsibility Model)**
  - **AWS 负责「云的安全」(Security OF the Cloud)**：物理设施、硬件、网络、虚拟化层、区域/可用区运维等。
  - **客户负责「云内的安全」(Security IN the Cloud)**：数据、应用、操作系统与补丁、网络与防火墙配置、身份与访问管理（IAM）、加密与凭证等。
  - 随服务类型不同责任划分不同：如 EC2 客户管 OS；Lambda/S3 等托管服务客户主要管数据和访问策略。
  - **控制类型 (Types of Controls)**：
    - **继承控制 (Inherited Controls)** — 客户完全继承自 AWS 的控制：
      - 物理与环境控制（数据中心物理安全、电力、冷却、消防）
    - **共享控制 (Shared Controls)** — AWS 与客户共同承担，但作用于不同层：
      - **补丁管理 (Patch Management)**：AWS 负责基础设施与托管服务的补丁；客户负责自己的 OS、应用补丁
      - **配置管理 (Configuration Management)**：AWS 配置基础设施设备；客户配置自己的 OS、数据库、应用
      - **意识与培训 (Awareness & Training)**：AWS 培训 AWS 员工；客户培训自己的员工
    - **客户特定控制 (Customer Specific Controls)** — 完全由客户负责：
      - 服务与通信保护（如数据在应用内的路由/分区）
      - 数据分类与加密选择
  - **考点**：Patch Management、Configuration Management、Awareness & Training 是典型的 **Shared Controls**，双方各自负责自己层面的部分。
- **Amazon Inspector** — 自动安全漏洞评估服务，对 EC2、容器、Lambda 等做漏洞与合规检查。
- **AWS Shield** — 托管 DDoS 防护，Standard 随账户默认开启；Advanced 提供更细粒度防护、成本保护与 24/7 DRT 支持。
- **Amazon GuardDuty** — 智能威胁检测服务，使用机器学习持续监控 AWS 账户与工作负载中的恶意活动。
  - **核心特点**：
    - **无代理、一键启用**：无需安装软件，启用后自动开始分析。
    - **多数据源分析**：
      - **VPC Flow Logs**：检测异常网络流量
      - **CloudTrail 事件**：检测可疑 API 调用
      - **DNS 日志**：检测恶意域名访问
      - **S3 数据事件**：检测可疑 S3 访问（可选）
      - **EKS 审计日志**：检测 Kubernetes 威胁（可选）
    - **机器学习**：自动建立基线，识别异常行为。
  - **威胁类型示例**：
    - 加密货币挖矿（EC2 被入侵）
    - 凭证泄露（异常 API 调用）
    - 恶意 IP/域名通信
    - 未授权访问（异常登录地点）
    - S3 数据泄露风险
  - **与其他服务配合**：
    - **EventBridge**：GuardDuty 发现威胁 → 触发 Lambda 自动响应（如隔离 EC2）
    - **Security Hub**：汇总 GuardDuty 发现，集中安全视图
  - **考点**：需要"智能威胁检测"或"检测恶意活动/入侵"时选 **GuardDuty**；需要"漏洞扫描"选 **Inspector**；需要"DDoS 防护"选 **Shield**；需要"Web 攻击防护"选 **WAF**。
- **AWS WAF (Web Application Firewall)** — Web 应用防火墙，在 CloudFront、ALB、API Gateway、AppSync 前根据规则过滤 HTTP/HTTPS 请求。
  - **核心概念**：
    - **Web ACL**：规则集合，附加到 CloudFront/ALB/API Gateway，定义允许/阻止/计数的流量。
    - **Rules（规则）**：匹配条件 + 动作（Allow/Block/Count/CAPTCHA）。
    - **Rule Groups**：可复用的规则组，可自建或使用 AWS 托管规则。
  - **可防护的攻击类型**：
    - **SQL 注入 (SQLi)**：检测恶意 SQL 语句
    - **跨站脚本 (XSS)**：检测恶意脚本注入
    - **IP 黑/白名单**：按 IP 地址或 CIDR 范围过滤
    - **地理位置封锁**：按国家/地区阻止流量
    - **速率限制 (Rate-based Rules)**：防止 HTTP Flood / DDoS
    - **Bot 控制**：识别并管理爬虫、自动化工具
  - **AWS 托管规则 (Managed Rules)**：
    - AWS 提供的预置规则集（如 Core Rule Set、SQL Database、Known Bad Inputs）
    - 第三方安全厂商规则（通过 AWS Marketplace）
  - **与 Shield 配合**：WAF 过滤应用层（L7）攻击，Shield 防护网络层（L3/L4）DDoS。
  - **考点**：需要"防止 SQL 注入/XSS"或"按 IP/地理位置过滤请求"或"速率限制"时选 **AWS WAF**；需要"DDoS 防护"选 **AWS Shield**。
- **AWS Config** — 记录资源配置历史与变更，用规则评估是否符合策略，支持合规审计与配置漂移检测。
- **AWS KMS (Key Management Service)** — 托管加密密钥服务，创建与管理 CMK（客户主密钥），供 S3、EBS、RDS 等服务及应用做数据加密与解密。
- **AWS CloudHSM** — 专用硬件安全模块（HSM），在 AWS 云中提供客户独占的加密硬件设备。
  - **核心特点**：
    - **专用硬件**：单租户 HSM 设备，客户完全控制密钥，AWS 无法访问。
    - **合规性**：满足 **FIPS 140-2 Level 3** 认证，适合严格合规要求（如金融、政府）。
    - **标准 API 支持**：PKCS#11、JCE、CNG，应用可直接集成。
    - **高可用**：可部署多个 HSM 组成集群，跨 AZ 复制。
  - **CloudHSM vs KMS**：
    | 特性 | KMS | CloudHSM |
    |------|-----|----------|
    | 管理方式 | AWS 托管 | 客户自管 |
    | 硬件 | 多租户共享 | **单租户专用** |
    | 密钥控制 | AWS 可在特定条件下访问 | **客户独占，AWS 无法访问** |
    | 合规 | FIPS 140-2 Level 2 | **FIPS 140-2 Level 3** |
    | 成本 | 按密钥/请求计费（低） | 按 HSM 实例小时计费（较高） |
    | 使用复杂度 | 简单 | 需要自行管理 |
  - **典型用例**：
    - SSL/TLS 私钥存储（如 Web 服务器证书密钥）
    - 数据库透明数据加密（TDE）密钥
    - 数字签名与 PKI
    - 合规要求严格的加密场景（PCI-DSS、HIPAA）
  - **考点**：需要"客户独占的硬件加密设备"或"FIPS 140-2 Level 3"或"AWS 无法访问密钥"时选 **CloudHSM**；一般加密需求选 **KMS**（更简单、成本低）。
- **AWS Artifact** — 按需获取 AWS 合规与安全报告、文档（如 SOC、ISO、PCI 等），用于审计与合规证明；与混合云连通性无关。
- **AWS Security Bulletins（安全公告）** — AWS 发布的安全通知与漏洞公告。
- **AWS Certificate Manager (ACM)** — 申请、部署与管理 SSL/TLS 证书（公有证书免费），与 ELB、CloudFront 等集成，用于 HTTPS 加密。

<!-- 填写本节知识点 -->

---

## Section 17: Machine Learning

- **Amazon Comprehend** — 自然语言处理（NLP）服务，用于文本分析（情感、实体、关键词、语言等），与资源变更审计无关。

- **Amazon Personalize** — 托管机器学习服务，用于构建个性化推荐系统。
<!-- 填写本节知识点 -->

---

## Section 18: Account Management, Billing & Support

- **AWS Organizations** — 多账户管理：将多个 AWS 账户组织成组织（OU）、统一账单、通过 SCP（服务控制策略）集中管控各账户可用的服务与权限。
  - **Consolidated Billing（合并账单）**：Organizations 的核心功能之一。
    - **单一付款方式**：所有成员账户的费用汇总到管理账户（Management Account），一张账单统一支付。
    - **批量折扣 (Volume Discounts)**：组织内所有账户的用量合并计算，更容易达到批量折扣阶梯（如 S3、数据传输）。
    - **RI / Savings Plans 共享**：一个账户购买的 Reserved Instances 或 Savings Plans，可自动应用到组织内其他账户的匹配用量，最大化利用率。
    - **成本分摊与标签**：可通过成本分配标签（Cost Allocation Tags）追踪各账户/项目的费用。
    - **免费功能**：Consolidated Billing 是 AWS Organizations 的免费功能。
  - **考点**：
    - 需要"多账户单一账单"或"跨账户共享 RI/Savings Plans"时使用 **Consolidated Billing（AWS Organizations）**。
    - 合并账单的主要好处：**单一付款、批量折扣、RI 共享**。
- **AWS Service Control Policies (SCPs)** — Organizations 中的集中权限护栏，限制成员账户/OU 可使用的最大权限。
  - **核心概念**：
    - SCP 是**权限边界**，不授予权限，只限制权限上限；实际权限 = SCP ∩ IAM 策略。
    - 附加到**组织根**、**OU** 或**成员账户**，向下继承。
    - **不影响管理账户（Management Account）**：管理账户不受 SCP 限制。
    - **不影响服务关联角色（Service-linked Roles）**：SCP 不限制服务关联角色的权限。
  - **典型用例**：
    - 禁止成员账户离开组织：`"Effect": "Deny", "Action": "organizations:LeaveOrganization"`
    - 限制可用区域：只允许在特定 Region 使用资源。
    - 禁止关闭 CloudTrail / GuardDuty 等安全服务。
    - 强制使用特定实例类型或服务。
  - **SCP vs IAM Policy**：
    - **IAM Policy**：授予或拒绝特定身份的权限。
    - **SCP**：为整个账户/OU 设置权限上限，即使 IAM Policy 允许，若 SCP 拒绝则无权限。
  - **默认行为**：新账户默认附加 `FullAWSAccess` SCP（允许一切）；若移除或改为白名单模式，需显式允许。
  - **考点**：需要"跨多个账户集中限制可用服务/区域/操作"时选 **SCP**；SCP 不授予权限，只能限制；管理账户不受 SCP 约束。
- **AWS Control Tower** — 多账户环境的自动化设置与治理服务，基于最佳实践快速建立安全的多账户架构。
 
- **AWS Budgets** — 设置成本或用量预算，在达到阈值时通过邮件/SNS 告警，也可设置预留或 Savings Plans 使用量预算。
- **AWS Pricing Calculator** — 按服务与用量估算在 AWS 上的成本，用于上线前或架构选型时的成本预估。
- **AWS Cost & Usage Reports** — 将详细的成本与用量数据导出到 S3（如按天/月），用于对账、分析或接入自有 BI/计费系统。
- **AWS Trusted Advisor** — 提供成本、性能、安全、容错、服务限额等方面的最佳实践建议（部分功能需支持计划）。
  - **五大检查类别**：
    - **成本优化 (Cost Optimization)**：识别闲置资源、未使用的 EC2 Reserved Instances、低利用率实例等。
    - **性能 (Performance)**：检查高利用率实例、CloudFront 配置、EBS 吞吐量优化等。
    - **安全 (Security)**：检查安全组规则、IAM 使用情况、MFA 启用、公开 S3 桶、暴露的 Access Keys 等。
    - **容错 (Fault Tolerance)**：检查 EBS 快照、RDS 备份、多 AZ 部署、Auto Scaling 配置等。
    - **服务限额 (Service Limits)**：监控接近或已达限额的服务（如 EC2 实例数量、EBS 卷数量）。
  - **支持计划与功能**：
    - **Basic / Developer**：仅 7 项核心检查（主要安全类：S3 桶权限、安全组端口、IAM 使用、MFA、EBS 公开快照、RDS 公开快照、服务限额）。
    - **Business / Enterprise / Enterprise On-Ramp**：完整 50+ 项检查 + API 访问 + CloudWatch 集成告警。
  - **考点**：需要"检查账户安全配置最佳实践"或"识别闲置资源降低成本"或"检查服务限额"时选 **AWS Trusted Advisor**。注意它是建议性的，不自动修复问题。
- **EC2 Instance Usage Report** — EC2 实例用量报告，用于查看使用情况与成本分析。
- **AWS Health Dashboard** — 展示与您账户相关的 AWS 服务运行状况、计划变更及影响您资源的故障与维护事件，便于提前应对。Organizations 下管理账户可查看整个组织的健康事件，成员账户仅看本账户。事件类型包括：Service（服务问题）、Account（影响您资源的故障）、Scheduled changes（计划内维护）等。
- **AWS Knowledge Center** — 自助知识库：操作指南、常见问题、最佳实践文档，用于自助排查与学习。
- **AWS Support Concierge Service** — 企业级支持计划中的专属服务团队，协助账单、账户与成本相关问题（属 Enterprise Support）。
- **Infrastructure Event Management (IEM)** — 企业级支持附加服务，在您计划的大型事件（如迁移、大促、上线）期间提供架构与运维协助。

### AWS Support Plans（支持计划）

| 计划 | 费用 | 技术支持渠道 | 响应时间（生产系统故障） | 主要特点 |
|------|------|--------------|--------------------------|----------|
| **Basic** | 免费 | 无技术支持（仅文档、论坛、Health Dashboard） | — | 7 项 Trusted Advisor 核心检查 |
| **Developer** | $29/月起 | 邮件（工作时间） | 12 小时（一般指导） | 1 人可开 Case，学习/测试环境 |
| **Business** | $100/月起 | 邮件 + 电话 + 聊天（24×7） | **1 小时** | 无限用户开 Case，完整 Trusted Advisor，API 访问 |
| **Enterprise On-Ramp** | $5,500/月起 | 邮件 + 电话 + 聊天（24×7） | **30 分钟** | TAM 池、Concierge、IEM（1 次/年） |
| **Enterprise** | $15,000/月起 | 邮件 + 电话 + 聊天（24×7） | **15 分钟**（业务关键） | 专属 TAM、Concierge、IEM（无限） |

- **AWS Business Support Plan 详解**：
  - **适用场景**：生产环境工作负载，需要 24×7 技术支持与快速响应。
  - **技术支持**：邮件、电话、聊天全天候支持（24/7），可联系 Cloud Support Engineers。
  - **响应时间**：
    - 一般指导：24 小时
    - 系统受损：12 小时
    - 生产系统受损：4 小时
    - **生产系统故障**：**1 小时**
  - **Trusted Advisor**：完整 50+ 项检查 + API 访问 + CloudWatch 告警集成。
  - **AWS Support API**：可通过 API 创建、管理、查询 Support Case。
  - **第三方软件支持**：操作系统、数据库、常用软件的互操作性指导。
  - **无限用户**：账户内所有 IAM 用户均可开 Case。
  - **不含**：TAM（技术客户经理）、Concierge、IEM（需 Enterprise 级别）。
  - **费用**：月费 $100 起，或 AWS 月费用的 10%/7%/5%（按用量递减），取两者较高值。
  - **考点**：需要"24×7 电话支持"或"生产系统 1 小时响应"或"完整 Trusted Advisor + API"时，最低需 **Business Support Plan**。

**考点（TCO）**：比较应用在 AWS 上运行与本地运行的总拥有成本（TCO）时，必须考虑 **Physical hardware（物理硬件）**：本地需计入服务器、存储、网络、机房、电力与维护等；上云后由 AWS 提供，按用量付费，无自有硬件采购与维护成本。

<!-- 填写本节知识点 -->

---

## Section 19: Advanced Identity

- **Amazon Cognito** — 为移动与 Web 应用提供用户注册、登录、找回密码等；支持社交/企业 IdP 联合身份（如 Google、SAML），可托管用户池（User Pools）或仅做身份联合（Identity Pools 获取临时 AWS 凭证）。

- **Amazon Cloud Directory** — 云原生的高扩展性目录服务，用于构建灵活的层级数据结构。
  - **核心特点**：
    - **多维层级结构**：支持多个层级维度（如按组织、位置、设备类型等），不局限于传统单一树结构。
    - **Schema 灵活**：可定义自定义属性与 Facet（面），同一对象可同时具有多个 Facet。
    - **高扩展性**：可存储数亿对象，适合大规模组织结构、设备目录、IoT 设备管理。
    - **完全托管**：无需管理基础设施，自动扩展。
  - **典型用例**：
    - 组织架构管理（员工、部门、汇报关系）
    - 设备与资产目录
    - 应用内的层级数据（如课程目录、产品分类）
  - **Cloud Directory vs Directory Service vs Cognito**：
    | 服务 | 用途 | 协议 |
    |------|------|------|
    | **Cloud Directory** | 应用自定义层级目录、灵活 schema | REST API |
    | **AWS Directory Service** | 托管 Microsoft AD / LDAP 兼容目录 | LDAP、Kerberos |
    | **Amazon Cognito** | 用户身份验证、社交登录、移动/Web 应用 | OAuth、OIDC、SAML |
  - **考点**：需要"为应用构建灵活的层级数据结构"时选 **Cloud Directory**；需要"Windows AD / LDAP 兼容"选 **AWS Directory Service**；需要"用户登录注册"选 **Cognito**。

- **AWS Directory Service** — 托管目录服务，提供 Microsoft AD 功能或与现有 AD 集成。
  - **三种模式**：
    - **AWS Managed Microsoft AD**：完整托管的 Windows AD，可与本地 AD 建立信任。
    - **AD Connector**：代理连接器，将 AWS 应用的认证请求转发到本地 AD，不存储用户。
    - **Simple AD**：轻量级 Samba 4 兼容目录，适合小规模、基本 AD 功能需求。
  - **典型用例**：EC2 Windows 实例加域、WorkSpaces 用户认证、RDS SQL Server Windows 认证、企业 SSO。
  - **考点**：需要"Windows AD 功能"或"EC2 加域"时选 **AWS Directory Service (Managed AD)**。

<!-- 填写本节知识点 -->

---

## Section 20: Other Services

- **AWS Cloud9** — 基于浏览器的云端 IDE，可在 AWS 环境中编写、运行与调试代码，与混合云连通性无关。
- **Amazon SES (Simple Email Service)** — 发送与接收邮件服务，用于事务邮件、营销邮件、通知等；支持 SMTP 或 API，可与 Lambda、SNS 等集成；非组件间消息队列，不负责“消息不丢失”场景。
- **Amazon Connect** — 云联络中心服务，提供全渠道客户服务（语音、聊天）。
  - **核心功能**：
    - **云端呼叫中心**：无需硬件，按使用量付费，分钟级部署。
    - **IVR（交互式语音应答）**：通过 Contact Flows 设计自动语音菜单。
    - **全渠道**：支持语音通话、网页聊天、任务管理。
    - **AI 集成**：与 Lex（聊天机器人）、Polly（语音合成）、Transcribe（语音转文字）、Comprehend（情感分析）集成。
    - **座席桌面**：内置 CCP（Contact Control Panel），座席可处理来电与聊天。
  - **典型用例**：客服热线、技术支持中心、预约系统、外呼营销。
  - **考点**：需要"云端客服中心/呼叫中心"时选 **Amazon Connect**；它**不是**消息队列（选 SQS）、不是通知服务（选 SNS）、不是邮件服务（选 SES）。
- **AWS Snowball** — 物理设备，用于大规模数据迁入/迁出 AWS（TB 到 PB 级）。适用于网络成本高或带宽不足时，将设备寄到现场拷贝数据后寄回 AWS 导入（如导入 S3）。另有 Snowball Edge（带计算能力）、Snowmobile（集装箱级海量迁移）。

<!-- 填写本节知识点 -->

---

## Section 21: AWS Architecting & Ecosystem

- **AWS Partner Solutions** — 由 AWS 与合作伙伴提供的预置解决方案，用自动化模板与脚本在 AWS 上快速部署常用技术（如 IBM MQ、SAP 等），用最少时间与精力即可上线。**考点**：要在 AWS 上以**最少精力与时间**部署流行技术（如 IBM MQ），应使用 **AWS Partner Solutions**。CloudWatch 为监控、OpsWorks 为配置管理、Aurora 为数据库，均非“一键部署第三方技术”的解决方案。

<!-- 填写本节知识点 -->

### AWS 重要政策与法律文档

- **AWS Acceptable Use Policy（可接受使用政策）** — 定义 AWS 服务的**禁止用途**。
  - **禁止的使用行为**：
    - 非法活动（违反法律法规）
    - 侵犯他人权利（知识产权、隐私）
    - 网络攻击（DDoS、黑客入侵、端口扫描）
    - 分发恶意软件、病毒
    - 发送垃圾邮件（Spam）
    - 未经授权访问系统
  - **考点**：需要"了解 AWS 服务禁止用途/prohibited uses"时查看 **AWS Acceptable Use Policy**。

- **AWS Service Terms（服务条款）** — AWS 各服务的具体使用条款与限制。

- **AWS Service Level Agreements (SLA)** — 服务级别协议，定义可用性承诺与补偿机制。
  - 例如：S3 Standard 承诺 99.9% 可用性，未达标可获得服务积分。

- **AWS Privacy Policy（隐私政策）** — 说明 AWS 如何收集、使用、保护客户数据。

| 文档 | 用途 | 考点关键词 |
|------|------|-----------|
| **Acceptable Use Policy** | 禁止用途 | prohibited uses |
| **Service Terms** | 服务条款 | terms of service |
| **SLA** | 可用性承诺 | availability, uptime |
| **Privacy Policy** | 数据隐私 | data privacy |
| **Artifact** | 合规报告下载 | SOC, ISO, PCI |

---

- **AWS Cloud Adoption Framework (AWS CAF)** — AWS 提供的云迁移与转型指导框架，帮助组织规划和执行云采用之旅。
  - **六大视角 (6 Perspectives)**：
    | 视角 | 关注领域 | 关键利益相关者 |
    |------|---------|---------------|
    | **Business（业务）** | 云投资的业务价值、ROI | CxO、业务经理、财务 |
    | **People（人员）** | 组织变革、技能培训、角色定义 | HR、人才管理、培训 |
    | **Governance（治理）** | 风险管理、合规、预算控制 | CIO、项目经理、架构师 |
    | **Platform（平台）** | 云架构、技术标准、Landing Zone | CTO、架构师、工程师 |
    | **Security（安全）** | 安全控制、合规、数据保护 | CISO、安全团队 |
    | **Operations（运营）** | 云运维、监控、事件管理 | IT 运维、SRE |
  - **四个转型领域 (Transformation Domains)**：
    - **Technology**：迁移和现代化基础设施、应用、数据
    - **Process**：数字化和自动化业务流程
    - **Organization**：重组团队、提升敏捷性
    - **Product**：重新构想业务模式、创造新价值
  - **CAF 行动计划**：评估当前状态 → 识别差距 → 制定路线图 → 执行转型
  - **考点**：需要"规划云迁移战略"或"组织云采用指导"时选 **AWS CAF**；它是**框架/指南**，不是具体的迁移工具（工具用 SMS、DMS、Migration Hub）。

- **AWS Well-Architected Framework** — 五大支柱（卓越运营、安全性、可靠性、性能效率、成本优化）+ 可持续性支柱，用于评估和改进架构。

<!-- 填写本节知识点 -->

---

## Section 22: Preparing for the Exam + Practice Exam

### 常考服务速记（选记）

| 服务                              | 一句话总结                                                                                           |
| --------------------------------- | ---------------------------------------------------------------------------------------------------- |
| **AWS Systems Manager**           | 规模化管理 EC2/混合环境：补丁、配置、运行命令、Session Manager 免 SSH；运维与合规（Section 11）。    |
| **AWS Organizations**             | 多账户管理：OU、统一账单、SCP 集中管控；合并账单下 RI/Savings Plans 优惠组织内共享（Section 18）。   |
| **AWS Artifact**                  | 按需下载合规与安全报告（SOC、ISO、PCI 等），用于审计与合规证明；非连通性、非消息队列（Section 16）。 |
| **AWS Certificate Manager (ACM)** | 申请与管理 SSL/TLS 证书（公有证书免费），与 ELB、CloudFront 等集成，用于 HTTPS（Section 16）。       |

### 存储服务速记（选记）

| 服务                    | 类型     | 一句话总结                                              |
| ----------------------- | -------- | ------------------------------------------------------- |
| **Amazon EBS**          | 块存储   | 单实例块设备，低延迟、可快照加密；Section 6。           |
| **Amazon EFS**          | 文件存储 | 多实例共享的 NFS 文件系统，按需扩展；Section 6。        |
| **Amazon S3**           | 对象存储 | 海量对象、多存储类、多 AZ 可选；Section 8。             |
| **AWS Storage Gateway** | 混合存储 | 本地网关对接 S3/卷/虚拟磁带，连接本地与云；Section 13。 |

### 全球 / 安全 / 数据 服务速记（选记）

| 服务                       | 一句话总结                                                                                    |
| -------------------------- | --------------------------------------------------------------------------------------------- |
| **AWS Global Accelerator** | 固定任播 IP + 全球网络，将流量路由到多区域端点（ALB/EC2/NLB），提升可用性与性能；Section 12。 |
| **Amazon CloudFront**      | CDN，边缘站点缓存与分发内容，降低延迟、减轻源站压力；Section 12。                             |
| **AWS Direct Connect**     | 本地到 AWS 的专用物理连接，混合云稳定带宽与低延迟；Section 15。                               |
| **AWS KMS**                | 托管加密密钥（CMK），供 S3、EBS、RDS 等做加解密；Section 16。                                 |
| **AWS Glue**               | 托管 ETL 与数据目录，发现/编目/转换数据，支撑 Athena、Redshift、EMR；Section 9。              |

### 监控 / 审计 / 流水线 速记（选记）

| 服务 | 一句话总结 |
|------|------------|
| **AWS CloudTrail** | API 调用审计日志，谁在何时对何资源做了何种操作；合规与变更审计；Section 14。 |
| **AWS CodePipeline** | CI/CD 流水线：源码→构建→测试→部署，可对接 CodeBuild、Lambda、ECS、CloudFormation；Section 11。 |
| **Amazon Inspector** | 自动安全漏洞与合规评估（EC2、容器、Lambda 等）；Section 16。 |
| **AWS X-Ray** | 分布式追踪，请求调用链与延迟，应用调试与性能分析；Section 14。 |

### E 系 / 消息 / 大数据 速记（选记）

| 服务 | 一句话总结 |
|------|------------|
| **Amazon EBS** | 块存储，单台 EC2 块设备，快照与加密；Section 6。 |
| **Amazon EFS** | 共享文件存储（NFS），多实例挂载、按需扩展；Section 6。 |
| **Amazon ECS** | 托管容器编排（Docker），EC2 或 Fargate 模式；Section 10。 |
| **Amazon EMR** | 托管大数据集群（Hadoop、Spark 等），大规模数据处理；Section 9。 |
| **Amazon SNS** | 发布/订阅与推送（Topic→多订阅者），事件通知、告警、扇出；Section 13。 |

### 身份 / 数据库 / 安全 速记（选记）

| 服务 | 一句话总结 |
|------|------------|
| **AWS IAM** | 身份与访问管理：用户、组、角色、策略，控制对 AWS 资源与 API 的访问；Section 4。 |
| **Amazon Cognito** | 应用端身份：用户注册/登录、社交/企业 IdP 联合、User Pools / Identity Pools（临时 AWS 凭证）；Section 19。 |
| **Amazon Aurora** | 托管关系型数据库，兼容 MySQL/PostgreSQL，高可用、多 AZ、自动扩展存储，适合 OLTP；Section 9。 |
| **AWS WAF** | Web 应用防火墙，在 CloudFront/ALB/API Gateway 前按规则过滤请求（IP、URI、SQL 注入、XSS）；Section 16。 |

### 存储与数据 速记（选记）

| 服务 | 一句话总结 |
|------|------------|
| **Amazon EBS** | 块存储，单台 EC2 块设备，快照与加密；Section 6。 |
| **Amazon S3 (Simple Storage Service)** | 对象存储，桶与对象、多存储类，适合海量对象、静态资源、备份；Section 8。 |
| **AWS Snowball** | 物理设备，大规模数据迁入/迁出（TB～PB），网络贵或带宽不足时用；Section 20。 |
| **Amazon DynamoDB** | 托管 NoSQL 键值/文档库，低延迟、自动扩展，适合高并发与 Serverless；Section 9。 |
| **Amazon Redshift** | 托管数据仓库（OLAP），列式存储、大规模并行查询，适合 BI、报表与分析；Section 9。 |

<!-- 填写本节知识点 -->

---

## Section 23: Congratulations - AWS Certified Cloud Practitioner

<!-- 填写本节知识点（如考前检查清单、证书领取等） -->

---

_最后更新：——_
