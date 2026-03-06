# AWS Certified Cloud Practitioner (CLF-C02) 知识点总结

> 按课程章节整理的复习模版，学习过程中在对应章节下方填写要点。

---

## Section 4: IAM - Identity and Access Management

- **AWS IAM** — 身份与访问管理服务，控制谁可以对哪些 AWS 资源与 API 执行哪些操作（用户、组、角色、策略）。

### 身份（Who）与策略（What）的关系

- **IAM policy（策略）** — 定义「能做什么」的 JSON 文档，是权限的载体。
  - 核心元素：**Effect**（Allow/Deny）、**Action**（如 `s3:GetObject`）、**Resource**（如某个桶/对象）、可选 **Condition**（IP、时间、MFA 等）。
  - 策略不单独存在，必须**附加到**某个身份或资源上才会生效。
  - 类型：**托管策略**（可复用、可挂到多身份）与**内联策略**（嵌在单一用户/角色/组上，随主体删除而消失）。

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
  承诺 1 年或 3 年使用，换取明显折扣。需预付或部分预付（All/Partial/No Upfront）。适合稳定、可预测的基线负载。与具体实例类型、可用区（或区域）绑定，视预留范围而定。

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

<!-- 填写本节知识点 -->

---

## Section 10: Other Compute Services (ECS, Lambda, Batch, Lightsail)

- **Amazon ECS (Elastic Container Service)** — 托管容器编排，运行 Docker 容器；支持 EC2 模式（自管节点）与 Fargate 模式（无服务器），与 ECR、ALB、CloudMap 等集成，适合微服务与容器化应用。
- **AWS Lambda** — 无服务器函数，按事件触发、按执行时长计费，无需管理服务器；适合事件驱动、API 后端、定时任务等。
- **AWS Batch** — 托管批处理作业调度，在 EC2 或 Fargate 上运行大规模批处理任务，适合 HPC、数据处理流水线。
- **Amazon Lightsail** — 轻量级 VPS：固定月费、简单控制台，适合小站、原型与入门场景。

<!-- 填写本节知识点 -->

---

## Section 11: Deployments & Managing Infrastructure at Scale

- **AWS CloudFormation** — 基础设施即代码（IaC）：用模板（YAML/JSON）定义一组 AWS 资源，一键部署、更新与删除（栈），便于版本控制与环境一致性。
- **Amazon Elastic Beanstalk** — PaaS：上传应用代码（如 Java、Node、Python、Docker），自动配置并管理底层 EC2、ELB、ASG、监控等，适合快速部署 Web 应用、无需手管基础设施。
- **AWS Systems Manager** — 在规模上管理 EC2 与混合环境：补丁、配置、运行命令、清单、Session Manager 免 SSH 登录等，统一运维与合规。
- **AWS OpsWorks** — 基于 Chef/Puppet 的配置管理与自动化，用于部署、配置、运维 EC2 等资源（如应用栈、层、实例），适合需要脚本化运维的场景。
- **AWS Server Migration Service (SMS)** — 将本地虚拟机（VM）批量迁移到 AWS（如迁到 EC2），支持增量复制与自动化调度。
- **AWS Application Discovery Service** — 发现并收集本地服务器、应用与依赖关系，用于迁移前的评估与规划（如配合 SMS、DMS、Migration Hub）。
- **AWS CodePipeline** — 托管 CI/CD 流水线：从源码（CodeCommit/GitHub 等）到构建（CodeBuild）、测试、部署的自动化流水线，可对接 Lambda、ECS、CloudFormation 等。

<!-- 填写本节知识点 -->

---

## Section 12: Leveraging the AWS Global Infrastructure

- **Amazon CloudFront** — CDN（内容分发网络），通过全球**边缘站点（Edge Locations）** 缓存并就近分发静态/动态内容，降低延迟、减轻源站压力；源站可为 S3、EC2、ELB 或自有服务器。
- **AWS Global Accelerator** — 使用 AWS 全球网络与固定任播 IP，将流量经最优路径路由到多区域端点（如 ALB、EC2、NLB），提升可用性与性能，适合需稳定入口 IP 或跨区域高可用的应用。

<!-- 填写本节知识点 -->

---

## Section 13: Cloud Integrations

- **AWS Storage Gateway** — 混合存储：在本地部署网关，将本地数据以文件（File Gateway→S3）、卷（Volume Gateway）或磁带（Tape Gateway）形式与 AWS 存储对接，便于备份、迁移与分层。
- **Amazon SQS (Simple Queue Service)** — 托管消息队列，解耦组件：消息持久化在队列中直到被成功处理并删除，若消费方故障则消息保留不丢，适合异步、削峰、可靠传递。**考点**：确保“组件间消息在组件故障时不丢失”应使用 **Amazon SQS**。
- **Amazon SNS (Simple Notification Service)** — 托管发布/订阅与推送：主题（Topic）发布消息，多个订阅者（HTTP/SQS/ Lambda/邮件/SMS 等）接收，适合事件通知、告警、扇出。
- **对比本题其他选项**：**AWS Direct Connect** 为混合云专线连接，与消息持久化无关；**Amazon SES** 为邮件收发，非组件间消息队列；**Amazon Connect** 为云联络中心（客服、IVR），非应用消息队列。

<!-- 填写本节知识点 -->

---

## Section 14: Cloud Monitoring

- **AWS CloudTrail** — API 调用审计与日志，记录谁在何时对何资源做了何种操作，用于合规、变更审计与故障排查；与混合云连通性无关。
- **AWS X-Ray** — 分布式追踪服务，分析请求在多个服务间的调用链与延迟，用于应用调试与性能分析，**不**用于资源变更审计。
- **考点（变更管理）**：审计与监控 AWS 环境中**资源变更**的两个服务是 **AWS Config**（配置历史与合规）和 **AWS CloudTrail**（API 操作日志）。Comprehend、Transit Gateway、X-Ray 不负责变更审计。

<!-- 填写本节知识点 -->

---

## Section 15: VPC & Networking

- **AWS Direct Connect** — 从本地数据中心到 AWS 的**专用物理连接**（不经过公网），低延迟、稳定带宽，适合混合云中需要可预测性能与大量数据传输的场景。
- **AWS VPN** — 通过互联网在本地网络与 VPC 之间建立加密隧道（如 Site-to-Site VPN），无需专线、部署快，适合混合云中“先连上”或带宽要求不极高的场景。
- **AWS Transit Gateway** — 区域级网络枢纽，用于连接多个 VPC 及本地网络（VPN/Direct Connect），集中路由。
- **考点（混合云连通性）**：构建混合云架构时，常用的两种**连通性**选项是 **AWS Direct Connect** 和 **AWS VPN**。Artifact、Cloud9、CloudTrail 与网络连通无关。

<!-- 填写本节知识点 -->

---

## Section 16: Security & Compliance

- **AWS 共享责任模型 (Shared Responsibility Model)**
  - **AWS 负责「云的安全」(Security OF the Cloud)**：物理设施、硬件、网络、虚拟化层、区域/可用区运维等。
  - **客户负责「云内的安全」(Security IN the Cloud)**：数据、应用、操作系统与补丁、网络与防火墙配置、身份与访问管理（IAM）、加密与凭证等。
  - 随服务类型不同责任划分不同：如 EC2 客户管 OS；Lambda/S3 等托管服务客户主要管数据和访问策略。
- **Amazon Inspector** — 自动安全漏洞评估服务，对 EC2、容器、Lambda 等做漏洞与合规检查。
- **AWS Shield** — 托管 DDoS 防护，Standard 随账户默认开启；Advanced 提供更细粒度防护、成本保护与 24/7 DRT 支持。
- **AWS WAF** — Web 应用防火墙，在 CloudFront、ALB、API Gateway 前根据规则过滤 HTTP/HTTPS 请求（如 IP、URI、SQL 注入、XSS）。
- **AWS Config** — 记录资源配置历史与变更，用规则评估是否符合策略，支持合规审计与配置漂移检测。
- **AWS KMS** — 托管加密密钥服务，创建与管理 CMK（客户主密钥），供 S3、EBS、RDS 等服务及应用做数据加密与解密。
- **AWS Artifact** — 按需获取 AWS 合规与安全报告、文档（如 SOC、ISO、PCI 等），用于审计与合规证明；与混合云连通性无关。
- **AWS Certificate Manager (ACM)** — 申请、部署与管理 SSL/TLS 证书（公有证书免费），与 ELB、CloudFront 等集成，用于 HTTPS 加密。

<!-- 填写本节知识点 -->

---

## Section 17: Machine Learning

- **Amazon Comprehend** — 自然语言处理（NLP）服务，用于文本分析（情感、实体、关键词、语言等），与资源变更审计无关。

<!-- 填写本节知识点 -->

---

## Section 18: Account Management, Billing & Support

- **AWS Organizations** — 多账户管理：将多个 AWS 账户组织成组织（OU）、统一账单、通过 SCP（服务控制策略）集中管控各账户可用的服务与权限。
- **AWS Budgets** — 设置成本或用量预算，在达到阈值时通过邮件/SNS 告警，也可设置预留或 Savings Plans 使用量预算。
- **AWS Pricing Calculator** — 按服务与用量估算在 AWS 上的成本，用于上线前或架构选型时的成本预估。
- **AWS Cost & Usage Reports** — 将详细的成本与用量数据导出到 S3（如按天/月），用于对账、分析或接入自有 BI/计费系统。
- **AWS Trusted Advisor** — 提供成本、性能、安全、容错、服务限额等方面的最佳实践建议（部分功能需支持计划）。
- **EC2 Instance Usage Report** — EC2 实例用量报告，用于查看使用情况与成本分析。
- **AWS Health Dashboard** — 展示与您账户相关的 AWS 服务运行状况、计划变更及影响您资源的故障与维护事件，便于提前应对。Organizations 下管理账户可查看整个组织的健康事件，成员账户仅看本账户。事件类型包括：Service（服务问题）、Account（影响您资源的故障）、Scheduled changes（计划内维护）等。
- **AWS Knowledge Center** — 自助知识库：操作指南、常见问题、最佳实践文档，用于自助排查与学习。
- **AWS Support Concierge Service** — 企业级支持计划中的专属服务团队，协助账单、账户与成本相关问题（属 Enterprise Support）。
- **Infrastructure Event Management (IEM)** — 企业级支持附加服务，在您计划的大型事件（如迁移、大促、上线）期间提供架构与运维协助。

**考点（TCO）**：比较应用在 AWS 上运行与本地运行的总拥有成本（TCO）时，必须考虑 **Physical hardware（物理硬件）**：本地需计入服务器、存储、网络、机房、电力与维护等；上云后由 AWS 提供，按用量付费，无自有硬件采购与维护成本。

<!-- 填写本节知识点 -->

---

## Section 19: Advanced Identity

- **Amazon Cognito** — 为移动与 Web 应用提供用户注册、登录、找回密码等；支持社交/企业 IdP 联合身份（如 Google、SAML），可托管用户池（User Pools）或仅做身份联合（Identity Pools 获取临时 AWS 凭证）。

<!-- 填写本节知识点 -->

---

## Section 20: Other Services

- **AWS Cloud9** — 基于浏览器的云端 IDE，可在 AWS 环境中编写、运行与调试代码，与混合云连通性无关。
- **Amazon SES (Simple Email Service)** — 发送与接收邮件服务，用于事务邮件、营销邮件、通知等；支持 SMTP 或 API，可与 Lambda、SNS 等集成；非组件间消息队列，不负责“消息不丢失”场景。
- **Amazon Connect** — 云联络中心服务（客服中心、IVR、全渠道等），与组件间消息传递、消息队列无关。
- **AWS Snowball** — 物理设备，用于大规模数据迁入/迁出 AWS（TB 到 PB 级）。适用于网络成本高或带宽不足时，将设备寄到现场拷贝数据后寄回 AWS 导入（如导入 S3）。另有 Snowball Edge（带计算能力）、Snowmobile（集装箱级海量迁移）。

<!-- 填写本节知识点 -->

---

## Section 21: AWS Architecting & Ecosystem

- **AWS Partner Solutions** — 由 AWS 与合作伙伴提供的预置解决方案，用自动化模板与脚本在 AWS 上快速部署常用技术（如 IBM MQ、SAP 等），用最少时间与精力即可上线。**考点**：要在 AWS 上以**最少精力与时间**部署流行技术（如 IBM MQ），应使用 **AWS Partner Solutions**。CloudWatch 为监控、OpsWorks 为配置管理、Aurora 为数据库，均非“一键部署第三方技术”的解决方案。

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
