import { Card, Empty, Typography } from "antd";

interface PlaceholderPageProps {
  title: string;
  description: string;
}

export function PlaceholderPage({ title, description }: PlaceholderPageProps) {
  return (
    <div className="page-stack">
      <Typography.Title level={2}>{title}</Typography.Title>
      <Card>
        <Empty description={description} />
      </Card>
    </div>
  );
}
