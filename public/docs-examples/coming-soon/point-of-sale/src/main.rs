use paykit_lib::{
    parse_payment_request_event_message, serialize_payment_request_event, EventId,
    PaykitAppId, PaymentAmount, PaymentEndpointIdentifier, PaymentReference,
    PaymentRequest, PaymentRequestEvent, PaymentRequestId, PaymentRequestTerms,
    PrivateApplicationMessage,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let app = PaykitAppId::new("demo-pos")?;
    // This amount is example data, not a conversion quote or invoice.
    let terms = PaymentRequestTerms::builder(
        PaymentAmount::new("10.00", "usd")?,
        PaymentReference::new("order-demo-001")?,
        vec![PaymentEndpointIdentifier::new("btc-lightning-bolt11")?],
    ).build()?;
    assert!(PaymentAmount::new("ten", "usd").is_err());
    let request_id = PaymentRequestId::new("11111111-1111-4111-8111-111111111111")?;
    let request = PaymentRequest::new(EventId::new_v4(), request_id.clone(), terms);
    let event = PaymentRequestEvent::Request(request);
    let raw_json = serialize_payment_request_event(&app, &event)?;
    // Local receive fixture only: no encrypted link is opened.
    let incoming = PrivateApplicationMessage {
        version: Some(1),
        kind: Some("paykit.payment_request".to_owned()),
        app_id: Some(app.as_str().to_owned()),
        raw_json,
    };
    let parsed = parse_payment_request_event_message(&incoming).expect("known request kind");
    assert!(parsed.is_valid());
    assert_eq!(parsed.payment_request_id(), Some(&request_id));
    assert_eq!(parsed.parsed_event(), Some(&event));
    println!("order-demo-001: request validated and parsed; no payment was sent.");
    Ok(())
}
