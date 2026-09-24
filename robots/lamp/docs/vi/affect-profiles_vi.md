# Profile sắc thái cảm xúc cho tracking

Phần khung ban đầu này thêm trạng thái sắc thái cảm xúc (affect) vào tracker hiện có. Tracker vẫn chọn và theo dõi cùng target bằng cùng luật điều khiển; affect thay đổi cách SmoothDamp follower phản ứng. Mặc định là `neutral`, giữ nguyên chính xác các tham số tracking hiện có.

Phần triển khai nằm trong `hal/drivers/tracking/affect.py` (`AffectState`) và được tích hợp vào `TrackerService`. Xem [Vision tracking](vision-tracking_vi.md) để biết bộ điều khiển nền tảng.

## Profile tương đối

Các profile ban đầu là `neutral`, `curious` và `fearful`. Mỗi profile cung cấp hệ số tương đối cho `smooth_time` và `max_speed`, thay vì sao chép các tham số pursuit và saccade cơ sở. `neutral` dùng hệ số giữ nguyên (1.0).

Ở mỗi nhịp tracking, service lấy các hằng số cơ sở hiện tại của chế độ pursuit hoặc saccade đã chọn, áp dụng hệ số affect, rồi áp dụng giới hạn tốc độ an toàn. Vì vậy, các thay đổi tuning cơ sở sau này được kế thừa bởi mọi profile. Affect không thể nâng tốc độ kết quả vượt giới hạn đó.

```text
Hằng số pursuit/saccade hiện tại
    → hệ số affect tương đối
    → giới hạn tốc độ an toàn
    → SmoothDamp follower hiện có
```

Các profile có tên chỉ là điểm khởi đầu thử nghiệm, chưa phải tham số đã hiệu chuẩn trên motor GL40 II thực tế. Thay đổi này không sửa hiệu chuẩn motor và không cần chuyển động phần cứng để kiểm thử.

## Trạng thái và chuyển tiếp

Cường độ nằm trong khoảng `0.0` đến `1.0`: mức không đưa các hệ số của profile về neutral, còn mức một áp dụng đầy đủ hệ số tương đối. Các giá trị ở giữa nội suy giữa hai đầu mút đó.

Thay đổi dùng đồng hồ đơn điệu và chuyển tiếp smoothstep. Các hệ số chuyển động được trộn trong thời lượng yêu cầu, thay vì thay thế đột ngột. Nếu có yêu cầu mới khi đang chuyển tiếp, quá trình mới bắt đầu từ các hệ số đang được trộn tại thời điểm đó.

Điểm gọi Python nội bộ là:

```python
tracker_service.set_affect(name, intensity=1.0, transition_s=0.5)
```

Ví dụ, một instance `TrackerService` hiện có có thể yêu cầu `"curious"` với cường độ một phần, rồi trở về `"neutral"`. Setter này cấu hình affect; đây không phải lệnh bắt đầu tracking hay di chuyển motor. Trạng thái tracking có thêm mục `affect` để bên gọi kiểm tra trạng thái.

## Phạm vi của bước này

Chưa có liên kết tự động với `/emotion`. HTTP setter và bộ chọn trên simulator cấu hình affect tracking riêng. Các biểu cảm cảm xúc hiện có vẫn tách biệt với trạng thái tracking này.

Việc chọn đối tượng chú ý cũng chưa thay đổi: bước này không triển khai thứ tự ưu tiên người → khuôn mặt → mắt hoặc ngắt tracking khi có vẫy tay. Tiến lại gần/chạm nhẹ khi tò mò và lùi lại/co người khi sợ là các quyết định hành vi trong tương lai, không phải kết quả của những hệ số chuyển động được thêm ở đây.

Đây chỉ là thay đổi phần khung trong repository. Bước này không bao gồm khởi động lại service hay triển khai.

### Điều khiển nguyên mẫu

Trình mô phỏng có `/servo/affect`: neutral (1,1), curious (0.85,1), calm (1.35,0.7), happy (0.9,1.05), sad (1.5,0.55), excited (0.75,1.15), fearful (0.65,1.25); mỗi cặp là hệ số thời gian làm mượt và tốc độ. Đây là động học tracking thử nghiệm, chưa phải độ lệch tư thế đã hiệu chuẩn. Chọn phong cách không di chuyển/bật lại motor hoặc khởi động tracking. Bản ghi `/emotion` vẫn độc lập. `/servo/output` trả affect và trạng thái khớp Pi, ghi rõ nguồn mock/driver.
