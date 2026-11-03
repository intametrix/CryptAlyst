package main

import (
	"fmt"
	"time"

	"pumpdump/backend/internal/envelope"
)

func main() {
	b := envelope.NewBus("127.0.0.1:61613", "cryptalyst", "cryptalyst")
	if err := b.Connect(); err != nil {
		fmt.Println("connect:", err)
		return
	}
	p, _, err := envelope.Run(envelope.Request{Topic: "intel.forecast", Key: "BTCUSDT", Producer: "forecast"},
		time.Now(), func() (any, error) { return map[string]any{"probe": true}, nil })
	if err != nil {
		fmt.Println("run:", err)
		return
	}
	fmt.Println("publishing to exchange", string(p.Meta.Plane), "key", p.Meta.Topic)
	if err := b.Publish(p); err != nil {
		fmt.Println("publish:", err)
		return
	}
	fmt.Println("published ok")
	_ = b.Close()
}
