-- 01_schema.sql — six insurance tables (SPEC §4.1)
-- Plain CREATE TABLE on purpose: seed.py rebuilds the public schema before
-- applying this file, so the SQL files stay the single source of truth.
-- Every table and column carries a bilingual COMMENT "中文 / English";
-- enum columns list all allowed values (source for schema_context.py, M3).

-- ---------------------------------------------------------------- products
CREATE TABLE products (
    product_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_code  text NOT NULL UNIQUE,
    product_name  text NOT NULL,
    category      text NOT NULL CONSTRAINT products_category_allowed
                       CHECK (category IN ('life', 'critical_illness', 'medical', 'accident', 'annuity')),
    term_years    integer NOT NULL,
    launched_date date NOT NULL,
    is_active     boolean NOT NULL
);
COMMENT ON TABLE products IS '保险产品表 / Insurance products';
COMMENT ON COLUMN products.product_id IS '产品ID，主键自增 / Product ID, auto-increment primary key';
COMMENT ON COLUMN products.product_code IS '产品编码，唯一，如 PRD-001 / Product code, unique, e.g. PRD-001';
COMMENT ON COLUMN products.product_name IS '产品名称 / Product name';
COMMENT ON COLUMN products.category IS '产品类别，取值：life=寿险、critical_illness=重疾险、medical=医疗险、accident=意外险、annuity=年金险 / Product category; values: life, critical_illness, medical, accident, annuity';
COMMENT ON COLUMN products.term_years IS '保障年期（年），0 表示终身 / Term in years; 0 means whole life';
COMMENT ON COLUMN products.launched_date IS '产品上市日期 / Product launch date';
COMMENT ON COLUMN products.is_active IS '是否在售 / Whether the product is actively sold';

-- ---------------------------------------------------------------- agents
CREATE TABLE agents (
    agent_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_code  text NOT NULL UNIQUE,
    name        text NOT NULL,
    branch_city text NOT NULL,
    hire_date   date NOT NULL
);
COMMENT ON TABLE agents IS '代理人表 / Agents';
COMMENT ON COLUMN agents.agent_id IS '代理人ID，主键自增 / Agent ID, auto-increment primary key';
COMMENT ON COLUMN agents.agent_code IS '代理人编码，唯一，如 AGT-001 / Agent code, unique, e.g. AGT-001';
COMMENT ON COLUMN agents.name IS '代理人姓名 / Agent name';
COMMENT ON COLUMN agents.branch_city IS '所属分公司城市 / Branch city of the agent';
COMMENT ON COLUMN agents.hire_date IS '入职日期 / Hire date';

-- ---------------------------------------------------------------- customers
CREATE TABLE customers (
    customer_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL,
    gender      text NOT NULL CONSTRAINT customers_gender_allowed CHECK (gender IN ('M', 'F')),
    birth_date  date NOT NULL,
    city        text NOT NULL,
    risk_level  text NOT NULL CONSTRAINT customers_risk_level_allowed
                    CHECK (risk_level IN ('low', 'medium', 'high')),
    created_at  timestamp NOT NULL
);
COMMENT ON TABLE customers IS '客户表 / Customers';
COMMENT ON COLUMN customers.customer_id IS '客户ID，主键自增 / Customer ID, auto-increment primary key';
COMMENT ON COLUMN customers.name IS '客户姓名 / Customer name';
COMMENT ON COLUMN customers.gender IS '性别，取值：M=男、F=女 / Gender; values: M (male), F (female)';
COMMENT ON COLUMN customers.birth_date IS '出生日期 / Birth date';
COMMENT ON COLUMN customers.city IS '所在城市 / City of residence';
COMMENT ON COLUMN customers.risk_level IS '风险等级，取值：low=低、medium=中、high=高 / Risk level; values: low, medium, high';
COMMENT ON COLUMN customers.created_at IS '客户建档时间 / Customer record creation timestamp';

-- ---------------------------------------------------------------- policies
CREATE TABLE policies (
    policy_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_no      text NOT NULL UNIQUE,
    customer_id    integer NOT NULL REFERENCES customers (customer_id),
    product_id     integer NOT NULL REFERENCES products (product_id),
    agent_id       integer NOT NULL REFERENCES agents (agent_id),
    status         text NOT NULL CONSTRAINT policies_status_allowed
                       CHECK (status IN ('in_force', 'lapsed', 'surrendered', 'expired')),
    effective_date date NOT NULL,
    expiry_date    date NOT NULL,
    sum_assured    numeric(14, 2) NOT NULL,
    annual_premium numeric(12, 2) NOT NULL
);
CREATE INDEX idx_policies_customer_id ON policies (customer_id);
CREATE INDEX idx_policies_product_id ON policies (product_id);
CREATE INDEX idx_policies_agent_id ON policies (agent_id);
COMMENT ON TABLE policies IS '保单表 / Insurance policies';
COMMENT ON COLUMN policies.policy_id IS '保单ID，主键自增 / Policy ID, auto-increment primary key';
COMMENT ON COLUMN policies.policy_no IS '保单号，唯一，如 POL-2023-00001 / Policy number, unique, e.g. POL-2023-00001';
COMMENT ON COLUMN policies.customer_id IS '客户ID，外键 → customers.customer_id / Customer ID, FK → customers.customer_id';
COMMENT ON COLUMN policies.product_id IS '产品ID，外键 → products.product_id / Product ID, FK → products.product_id';
COMMENT ON COLUMN policies.agent_id IS '代理人ID，外键 → agents.agent_id / Agent ID, FK → agents.agent_id';
COMMENT ON COLUMN policies.status IS '保单状态，取值：in_force=有效、lapsed=失效、surrendered=已退保、expired=已到期 / Policy status; values: in_force, lapsed, surrendered, expired';
COMMENT ON COLUMN policies.effective_date IS '保单生效日期 / Policy effective date';
COMMENT ON COLUMN policies.expiry_date IS '保单到期日期 / Policy expiry date';
COMMENT ON COLUMN policies.sum_assured IS '保额 / Sum assured';
COMMENT ON COLUMN policies.annual_premium IS '年缴保费 / Annual premium';

-- ---------------------------------------------------------------- claims
CREATE TABLE claims (
    claim_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_no        text NOT NULL UNIQUE,
    policy_id       integer NOT NULL REFERENCES policies (policy_id),
    filed_date      date NOT NULL,
    status          text NOT NULL CONSTRAINT claims_status_allowed
                        CHECK (status IN ('pending', 'approved', 'rejected', 'paid')),
    claimed_amount  numeric(14, 2) NOT NULL,
    approved_amount numeric(14, 2),
    closed_date     date
);
CREATE INDEX idx_claims_policy_id ON claims (policy_id);
COMMENT ON TABLE claims IS '理赔表 / Claims';
COMMENT ON COLUMN claims.claim_id IS '理赔ID，主键自增 / Claim ID, auto-increment primary key';
COMMENT ON COLUMN claims.claim_no IS '理赔号，唯一，如 CLM-2023-00001 / Claim number, unique, e.g. CLM-2023-00001';
COMMENT ON COLUMN claims.policy_id IS '保单ID，外键 → policies.policy_id / Policy ID, FK → policies.policy_id';
COMMENT ON COLUMN claims.filed_date IS '理赔申请日期 / Claim filing date';
COMMENT ON COLUMN claims.status IS '理赔状态，取值：pending=待处理、approved=已核准、rejected=已拒赔、paid=已赔付 / Claim status; values: pending, approved, rejected, paid';
COMMENT ON COLUMN claims.claimed_amount IS '申请理赔金额 / Claimed amount';
COMMENT ON COLUMN claims.approved_amount IS '核准赔付金额，未出核定结果时为空 / Approved amount, NULL until decided';
COMMENT ON COLUMN claims.closed_date IS '结案日期，未结案为空 / Closed date, NULL while open';

-- ---------------------------------------------------------------- payments
CREATE TABLE payments (
    payment_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy_id  integer NOT NULL REFERENCES policies (policy_id),
    period_no  integer NOT NULL,
    due_date   date NOT NULL,
    paid_date  date,
    amount     numeric(12, 2) NOT NULL,
    method     text NOT NULL CONSTRAINT payments_method_allowed
                   CHECK (method IN ('bank_transfer', 'alipay', 'wechat', 'cash')),
    status     text NOT NULL CONSTRAINT payments_status_allowed
                   CHECK (status IN ('paid', 'pending', 'overdue'))
);
CREATE INDEX idx_payments_policy_id ON payments (policy_id);
COMMENT ON TABLE payments IS '缴费记录表 / Payment records';
COMMENT ON COLUMN payments.payment_id IS '缴费记录ID，主键自增 / Payment ID, auto-increment primary key';
COMMENT ON COLUMN payments.policy_id IS '保单ID，外键 → policies.policy_id / Policy ID, FK → policies.policy_id';
COMMENT ON COLUMN payments.period_no IS '缴费期数，第几期 / Payment period number';
COMMENT ON COLUMN payments.due_date IS '应缴日期 / Due date';
COMMENT ON COLUMN payments.paid_date IS '实缴日期，未缴时为空 / Paid date, NULL if unpaid';
COMMENT ON COLUMN payments.amount IS '缴费金额 / Payment amount';
COMMENT ON COLUMN payments.method IS '缴费方式，取值：bank_transfer=银行转账、alipay=支付宝、wechat=微信、cash=现金 / Payment method; values: bank_transfer, alipay, wechat, cash';
COMMENT ON COLUMN payments.status IS '缴费状态，取值：paid=已缴、pending=待缴、overdue=逾期 / Payment status; values: paid, pending, overdue';
