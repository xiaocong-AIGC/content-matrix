import type { ReactNode } from "react";

import { Select, type SelectOption } from "./Select";

export type FilterOption = SelectOption;

/**
 * 页面顶部那排筛选控件（平台 / 城市 / 时间 / 账号…）。
 *
 * 现在它只是 `Select` 的一层壳 —— 签名一个字没改，所以 4 个现有调用点
 * （Overview / ContentLibrary / DeviceTasksView）一行都不用动，
 * 但白拿了 Select 的搜索、Esc 关闭和空态。
 *
 * 收口是分批做的：先让这一套统一到 Select，再逐个文件替换那 3 处 datalist
 * 和 12 处原生 select。一次性重构一个在生产上跑着的系统，风险不值当。
 */
export function FilterDropdown({
  label,
  value,
  options,
  onChange,
  icon,
}: {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
  icon?: ReactNode;
}) {
  return (
    <Select
      label={label}
      value={value}
      options={options}
      onChange={onChange}
      icon={icon}
      variant="filter"
    />
  );
}
